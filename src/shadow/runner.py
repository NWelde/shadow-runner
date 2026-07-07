"""Run a shadow experiment: try a proposed CI change on a throwaway fork.

Two halves:

* ``propose_yaml_change`` is a pure, formatting-preserving text edit that removes
  one entry from a job's ``needs:`` list. The rest of the file (comments,
  spacing, key order) is left byte-for-byte intact, so the result is suitable for
  a real pull request diff.

* ``run_shadow_experiment`` forks the repo, pushes the baseline config to the
  fork's default branch and the proposed config to a second branch, dispatches a
  run of each, records both, then deletes the fork once both finish. The fork is
  disposable, so the *pushed* files are normalised (a ``workflow_dispatch``
  trigger is ensured) — formatting only matters for the PR, which uses the
  ``propose_yaml_change`` output instead.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import datetime, timezone

import yaml

from ingest import github
from shadow.observe import compare_runs, poll_until_complete, run_duration_s
from store import save_experiment, save_repo, save_run

PROPOSED_BRANCH = "shadow-proposed"
FORK_READY_TIMEOUT_S = 60
RUN_APPEAR_TIMEOUT_S = 120


# ---------------------------------------------------------------------------
# propose_yaml_change — pure, formatting-preserving edit
# ---------------------------------------------------------------------------


def _as_list(needs) -> list[str]:
    """Normalise a ``needs`` value (None / str / list) to a list of strings."""
    if needs is None:
        return []
    if isinstance(needs, str):
        return [needs]
    return list(needs)


def propose_yaml_change(
    original_yaml: str, job_to_unblock: str, dep_to_remove: str
) -> str:
    """Return ``original_yaml`` with ``dep_to_remove`` dropped from one job's needs.

    Removes ``dep_to_remove`` from ``job_to_unblock``'s ``needs:`` list, leaving
    every other dependency and all surrounding formatting untouched. If the job
    has no such dependency the text is returned unchanged. Raises ``ValueError``
    if ``job_to_unblock`` is not a job in the workflow.
    """
    config = yaml.safe_load(original_yaml) or {}
    jobs = config.get("jobs", {})
    if job_to_unblock not in jobs:
        raise ValueError(f"job {job_to_unblock!r} not found in workflow")
    needs_list = _as_list((jobs[job_to_unblock] or {}).get("needs"))
    if dep_to_remove not in needs_list:
        return original_yaml
    remaining = [n for n in needs_list if n != dep_to_remove]
    return _rewrite_needs(original_yaml, job_to_unblock, remaining)


def _indent_of(line: str) -> int:
    """Number of leading spaces on a line (ignoring its trailing newline)."""
    return len(line) - len(line.lstrip())


def _find_jobs_block(lines: list[str]) -> tuple[int, int]:
    """Return ``(line index, indent)`` of the top-level ``jobs:`` key."""
    for i, line in enumerate(lines):
        if re.match(r"^\s*jobs:\s*$", line):
            return i, _indent_of(line)
    raise ValueError("no top-level 'jobs:' block found")


def _job_key_indent(lines: list[str], jobs_idx: int, jobs_indent: int) -> int:
    """Return the indent at which job ids sit (first child of ``jobs:``)."""
    for line in lines[jobs_idx + 1 :]:
        if not line.strip():
            continue
        indent = _indent_of(line)
        if indent > jobs_indent:
            return indent
        break
    raise ValueError("'jobs:' block is empty")


def _find_job_line(lines: list[str], start: int, key_indent: int, job: str) -> int:
    """Return the line index of ``job``'s key at exactly ``key_indent`` spaces."""
    pattern = re.compile(rf"^ {{{key_indent}}}{re.escape(job)}:\s*(?:#.*)?$")
    for i in range(start + 1, len(lines)):
        if pattern.match(lines[i]):
            return i
    raise ValueError(f"job {job!r} key line not found")


def _find_needs_line(lines: list[str], job_line: int, key_indent: int):
    """Return the index of the job's ``needs:`` line, or ``None`` if it has none."""
    for i in range(job_line + 1, len(lines)):
        line = lines[i]
        if not line.strip():
            continue
        if _indent_of(line) <= key_indent:
            break
        if re.match(r"^\s+needs\s*:", line):
            return i
    return None


def _render_inline(indent: int, remaining: list[str], eol: str) -> list[str]:
    """Render a replacement inline ``needs: [...]`` line (empty -> drop it)."""
    if not remaining:
        return []
    return [f"{' ' * indent}needs: [{', '.join(remaining)}]{eol}"]


def _render_block(
    indent: int, item_indent: int, remaining: list[str], eol: str
) -> list[str]:
    """Render a replacement block-style ``needs:`` list (empty -> drop it all)."""
    if not remaining:
        return []
    out = [f"{' ' * indent}needs:{eol}"]
    out += [f"{' ' * item_indent}- {dep}{eol}" for dep in remaining]
    return out


def _block_list_extent(lines: list[str], needs_idx: int, needs_indent: int):
    """Return ``(end index, item indent)`` covering a block ``needs:`` list."""
    end, item_indent = needs_idx + 1, needs_indent + 2
    while end < len(lines):
        line = lines[end]
        if not line.strip():
            end += 1
            continue
        if _indent_of(line) > needs_indent and line.lstrip().startswith("-"):
            item_indent = _indent_of(line)
            end += 1
        else:
            break
    return end, item_indent


def _rewrite_needs(text: str, job: str, remaining: list[str]) -> str:
    """Rewrite one job's ``needs:`` to ``remaining``, preserving other lines."""
    lines = text.splitlines(keepends=True)
    jobs_idx, jobs_indent = _find_jobs_block(lines)
    key_indent = _job_key_indent(lines, jobs_idx, jobs_indent)
    job_line = _find_job_line(lines, jobs_idx, key_indent, job)
    needs_idx = _find_needs_line(lines, job_line, key_indent)
    if needs_idx is None:
        return text

    needs_line = lines[needs_idx]
    needs_indent = _indent_of(needs_line)
    eol = "\n" if needs_line.endswith("\n") else ""
    value = needs_line.split("needs", 1)[1].lstrip()[1:]  # text after the colon
    value = value.split("#", 1)[0].strip()

    if value == "":  # block-list form
        end, item_indent = _block_list_extent(lines, needs_idx, needs_indent)
        replacement = _render_block(needs_indent, item_indent, remaining, eol)
        return "".join(lines[:needs_idx] + replacement + lines[end:])
    replacement = _render_inline(needs_indent, remaining, eol)
    return "".join(lines[:needs_idx] + replacement + lines[needs_idx + 1 :])


# ---------------------------------------------------------------------------
# run_shadow_experiment — live GitHub Actions experiment on a fork
# ---------------------------------------------------------------------------


def _merge_dispatch(on) -> dict:
    """Return an ``on:`` mapping that includes a ``workflow_dispatch`` trigger."""
    if on is None:
        merged: dict = {}
    elif isinstance(on, str):
        merged = {on: None}
    elif isinstance(on, list):
        merged = {event: None for event in on}
    else:
        merged = dict(on)
    merged.setdefault("workflow_dispatch", None)
    return merged


def _ensure_workflow_dispatch(raw_yaml: str) -> str:
    """Return ``raw_yaml`` re-serialised with a ``workflow_dispatch`` trigger.

    Only used for the disposable files pushed to the fork, so re-serialising
    (which drops comments/formatting) is acceptable here.
    """
    config = yaml.safe_load(raw_yaml) or {}
    # PyYAML parses the bare key ``on`` as the boolean ``True`` (YAML 1.1).
    on = config.pop("on", config.pop(True, None))
    config["on"] = _merge_dispatch(on)
    return yaml.safe_dump(config, sort_keys=False)


def _wait_for_fork(fork_owner: str, repo: str) -> None:
    """Block until a freshly-created fork is queryable, or time out."""
    deadline = time.time() + FORK_READY_TIMEOUT_S
    while time.time() < deadline:
        if github.fetch_repo_info_safe(fork_owner, repo) is not None:
            return
        time.sleep(3)
    raise TimeoutError(f"fork {fork_owner}/{repo} was not ready in time")


def _wait_for_run(fork_owner: str, repo: str, workflow_id: int, branch: str) -> dict:
    """Poll until the dispatched run on ``branch`` appears; return its dict."""
    deadline = time.time() + RUN_APPEAR_TIMEOUT_S
    while time.time() < deadline:
        run = github.find_latest_run_for_branch(fork_owner, repo, workflow_id, branch)
        if run is not None:
            return run
        time.sleep(5)
    raise TimeoutError(f"dispatched run on {branch} never appeared")


def _push_both_configs(
    fork_owner: str,
    repo: str,
    path: str,
    default_branch: str,
    original_yaml: str,
    proposed_yaml: str,
) -> None:
    """Commit the baseline to the default branch and proposed to a new branch."""
    github.put_file(
        fork_owner,
        repo,
        path,
        _ensure_workflow_dispatch(original_yaml),
        "shadow: baseline config",
        default_branch,
    )
    _, head_sha = github.get_default_branch_sha(fork_owner, repo)
    github.create_branch(fork_owner, repo, PROPOSED_BRANCH, head_sha)
    github.put_file(
        fork_owner,
        repo,
        path,
        _ensure_workflow_dispatch(proposed_yaml),
        "shadow: proposed config",
        PROPOSED_BRANCH,
    )


def _launch_runs(
    fork_owner: str, repo: str, workflow_filename: str, default_branch: str
) -> tuple[dict, dict]:
    """Dispatch baseline and proposed runs; return their (appeared) run dicts."""
    workflow_id = github.get_workflow_id(fork_owner, repo, workflow_filename)
    github.trigger_workflow_dispatch(fork_owner, repo, workflow_id, default_branch)
    baseline = _wait_for_run(fork_owner, repo, workflow_id, default_branch)
    github.trigger_workflow_dispatch(fork_owner, repo, workflow_id, PROPOSED_BRANCH)
    proposed = _wait_for_run(fork_owner, repo, workflow_id, PROPOSED_BRANCH)
    return baseline, proposed


def _persist_experiment(
    conn: sqlite3.Connection,
    owner: str,
    repo: str,
    hypothesis: str,
    baseline: dict,
    proposed: dict,
    comparison: dict,
) -> dict:
    """Save the repo, both run rows, and the experiment row; return the row dict."""
    repo_info = github.fetch_repo_info(owner, repo)
    repo_id = save_repo(conn, owner, repo, repo_id=repo_info["id"])
    now = datetime.now(timezone.utc).isoformat()
    baseline_id = save_run(conn, repo_id, str(baseline["id"]), "completed", now)
    proposed_id = save_run(conn, repo_id, str(proposed["id"]), "completed", now)
    exp_id = save_experiment(
        conn,
        repo_id,
        hypothesis,
        baseline_id,
        proposed_id,
        comparison["baseline_duration_s"],
        comparison["proposed_duration_s"],
        comparison["verdict"],
    )
    return {
        "id": exp_id,
        "repo_id": repo_id,
        "hypothesis": hypothesis,
        "baseline_run_id": baseline_id,
        "proposed_run_id": proposed_id,
        **comparison,
    }


def run_shadow_experiment(
    owner: str,
    repo: str,
    workflow_filename: str,
    original_yaml: str,
    proposed_yaml: str,
    conn: sqlite3.Connection,
) -> dict:
    """Fork, run both configs on real infrastructure, measure, persist, clean up.

    Forks ``owner/repo`` under the token's account, pushes the baseline to the
    fork's default branch and the proposed config to a ``shadow-proposed``
    branch, dispatches a run of each, waits for both to finish, compares them,
    saves the experiment to SQLite, deletes the fork, and returns the experiment
    row dict (including the comparison from ``observe.compare_runs``).
    """
    fork_owner = github.get_authenticated_user(owner_fallback=owner)
    github.fork_repo(owner, repo)
    _wait_for_fork(fork_owner, repo)
    # Forks have Actions disabled by default; enable so dispatch works.
    github.set_actions_enabled(fork_owner, repo, True)
    default_branch, _ = github.get_default_branch_sha(fork_owner, repo)
    path = f".github/workflows/{workflow_filename}"

    try:
        _push_both_configs(
            fork_owner, repo, path, default_branch, original_yaml, proposed_yaml
        )
        baseline_run, proposed_run = _launch_runs(
            fork_owner, repo, workflow_filename, default_branch
        )
        baseline_done = poll_until_complete(fork_owner, repo, baseline_run["id"])
        proposed_done = poll_until_complete(fork_owner, repo, proposed_run["id"])
        comparison = compare_runs(
            {"duration_s": run_duration_s(baseline_done), **baseline_done},
            {"duration_s": run_duration_s(proposed_done), **proposed_done},
        )
        hypothesis = (
            f"Removing an unnecessary dependency in {workflow_filename} lets jobs "
            "run in parallel and shortens the pipeline."
        )
        return _persist_experiment(
            conn, owner, repo, hypothesis, baseline_done, proposed_done, comparison
        )
    finally:
        github.delete_repo(fork_owner, repo)
