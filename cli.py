"""CI Shadow Runner — command-line entry point.

Four subcommands mirror the pipeline stages:

    ingest   pull a repo's CI data from GitHub into SQLite
    triage   read SQLite, derive signals, ask the model for findings
    shadow   propose a change and (optionally) run the shadow experiment
    pitch    write the report + PR body for the most recent experiment

Run from the repo root, e.g.::

    uv run python cli.py ingest --repo django/django
    uv run python cli.py shadow --repo django/django --dry-run
"""

from __future__ import annotations

import json
import os
import sys

# Standalone scripts must put ``src`` on the path (tests get this via pytest's
# pythonpath setting). Insert at the front so ``store``, ``dag`` etc. resolve.
_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_ROOT, "src"))

import click  # noqa: E402

from dag.graph import build_weighted_graph, parallelism_opportunities  # noqa: E402
from pipeline import build_repo_profile  # noqa: E402
from shadow.runner import propose_yaml_change, run_shadow_experiment  # noqa: E402
from store import (  # noqa: E402
    connect,
    init_db,
    load_repo_profile,
    write_repo_profile,
)
from triage.repo_profile_input import build_triage_input  # noqa: E402

DB_PATH = os.path.join(_ROOT, "data", "shadow.db")
OUTPUTS_DIR = os.path.join(_ROOT, "outputs")


def _split_repo(repo: str) -> tuple[str, str]:
    """Split an ``owner/name`` argument into a tuple, erroring on bad input."""
    if "/" not in repo:
        raise click.BadParameter("expected owner/repo, e.g. django/django")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise click.BadParameter("expected owner/repo, e.g. django/django")
    return owner, name


def _open_db():
    """Open the project SQLite database, creating its tables if needed."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = connect(DB_PATH)
    init_db(conn)
    return conn


def _workflow_yaml(profile, filename: str) -> str:
    """Return the raw YAML of the workflow named ``filename`` in ``profile``."""
    for workflow in profile.workflows:
        if workflow.filename == filename:
            return workflow.raw_yaml
    raise click.ClickException(f"workflow {filename!r} not found in stored profile")


def _pick_candidate(triage_input: dict):
    """Pick a (job_to_unblock, dep_to_remove) edge to drop, or ``None``.

    First choice: the last real dependency edge on the critical path — removing
    it lets the heaviest tail job start without waiting, the change most likely
    to shorten the pipeline. Fallback (when no critical-path edge exists): unblock
    the heaviest job that has any dependency at all, dropping its slowest one.
    Returns ``None`` only when no job in the workflow has a ``needs:`` edge.
    """
    dep_graph = triage_input.get("dep_graph", {})
    durations = triage_input.get("avg_durations", {})
    critical_path = triage_input.get("critical_path", [])
    for i in range(len(critical_path) - 1, 0, -1):
        job, prev = critical_path[i], critical_path[i - 1]
        if prev in dep_graph.get(job, []):
            return job, prev

    best = None  # (weight, job, dep)
    for job, deps in dep_graph.items():
        if not deps:
            continue
        weight = durations.get(job, 0.0)
        if best is None or weight > best[0]:
            dep = max(deps, key=lambda d: durations.get(d, 0.0))
            best = (weight, job, dep)
    return None if best is None else (best[1], best[2])


@click.group()
def cli() -> None:
    """Analyse, experiment on, and pitch CI pipeline speedups."""


@cli.command()
@click.option("--repo", required=True, help="owner/repo e.g. django/django")
def ingest(repo: str) -> None:
    """Pull a repo's CI data from GitHub and save it to SQLite."""
    owner, name = _split_repo(repo)
    click.echo(f"Fetching CI profile for {owner}/{name} ...")
    profile = build_repo_profile(owner, name)
    conn = _open_db()
    write_repo_profile(conn, profile)
    conn.close()
    jobs = sum(len(run.jobs) for run in profile.runs)
    click.echo(
        f"Saved repo {profile.id}: {len(profile.workflows)} workflows, "
        f"{len(profile.runs)} runs, {jobs} jobs."
    )


@cli.command()
@click.option("--repo", required=True)
def triage(repo: str) -> None:
    """Load from SQLite, derive signals, and print model findings."""
    owner, name = _split_repo(repo)
    conn = _open_db()
    profile = load_repo_profile(conn, owner, name)
    conn.close()
    triage_input = build_triage_input(profile)
    click.echo("Critical path: " + " -> ".join(triage_input["critical_path"]))
    click.echo(f"Critical-path time: {triage_input['critical_path_duration_s']:.1f}s\n")
    from triage.triage import run_triage  # lazy: only needs ollama here

    findings = run_triage(triage_input)
    click.echo(f"{len(findings)} finding(s):")
    for finding in findings:
        click.echo(json.dumps(finding.model_dump(), indent=2))


@cli.command()
@click.option("--repo", required=True)
@click.option("--dry-run", is_flag=True, default=False)
def shadow(repo: str, dry_run: bool) -> None:
    """Propose a change and run the shadow experiment (or just preview it)."""
    owner, name = _split_repo(repo)
    conn = _open_db()
    profile = load_repo_profile(conn, owner, name)
    triage_input = build_triage_input(profile)
    candidate = _pick_candidate(triage_input)
    if candidate is None:
        conn.close()
        raise click.ClickException("no removable dependency edge found to test")

    job, dep = candidate
    filename = triage_input["critical_path_workflow"]
    original = _workflow_yaml(profile, filename)
    proposed = propose_yaml_change(original, job, dep)
    click.echo(f"Proposed change in {filename}: drop '{dep}' from '{job}'.needs\n")

    if dry_run:
        conn.close()
        _print_opportunities(triage_input)
        click.echo("--- proposed workflow ---")
        click.echo(proposed)
        return

    result = run_shadow_experiment(owner, name, filename, original, proposed, conn)
    conn.close()
    click.echo("Experiment complete:")
    click.echo(json.dumps(result, indent=2, default=str))


def _print_opportunities(triage_input: dict) -> None:
    """Print the graph's parallelism opportunities (used by ``--dry-run``)."""
    graph = build_weighted_graph(
        triage_input["dep_graph"], triage_input["avg_durations"]
    )
    opportunities = parallelism_opportunities(graph, triage_input["avg_durations"])
    click.echo(f"{len(opportunities)} parallelism opportunity(ies) detected.\n")


@cli.command()
@click.option("--repo", required=True)
def pitch(repo: str) -> None:
    """Write the report + PR body for the most recent experiment to outputs/."""
    owner, name = _split_repo(repo)
    conn = _open_db()
    profile = load_repo_profile(conn, owner, name)
    conn.close()
    if not profile.experiments:
        raise click.ClickException("no experiments stored — run `shadow` first")

    experiment = profile.experiments[-1]
    comparison = _comparison_from_experiment(experiment)
    from pitch.pitch import write_pitch  # lazy: only needs ollama here

    artifacts = write_pitch(profile, experiment, comparison)
    _save_pitch(owner, name, artifacts)


def _comparison_from_experiment(experiment) -> dict:
    """Rebuild a comparison dict from a stored experiment row."""
    baseline = experiment.baseline_duration_s
    proposed = experiment.proposed_duration_s
    saving = baseline - proposed
    return {
        "baseline_duration_s": baseline,
        "proposed_duration_s": proposed,
        "saving_s": saving,
        "saving_pct": (saving / baseline * 100) if baseline > 0 else 0.0,
        "proposed_passed": experiment.outcome != "failure",
        "verdict": experiment.outcome or "unknown",
    }


def _save_pitch(owner: str, name: str, artifacts: dict) -> None:
    """Write the report and PR body to ``outputs/`` and echo their paths."""
    os.makedirs(OUTPUTS_DIR, exist_ok=True)
    stem = f"{owner}_{name}"
    report_path = os.path.join(OUTPUTS_DIR, f"{stem}_report.md")
    pr_path = os.path.join(OUTPUTS_DIR, f"{stem}_pr.md")
    with open(report_path, "w", encoding="utf-8") as handle:
        handle.write(artifacts["report"])
    with open(pr_path, "w", encoding="utf-8") as handle:
        handle.write(artifacts["pr_body"])
    click.echo(f"Wrote {report_path}\nWrote {pr_path}")


if __name__ == "__main__":
    cli()
