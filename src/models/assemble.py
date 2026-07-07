"""Assemble flat database rows into a nested ``RepoProfile``.

This is the pure, in-memory counterpart to the SQL reassembly in ``store``:
given the five flat row sets for one repo (repo, workflows, runs, jobs,
experiments) it nests each job under its run and wires every experiment to its
already-built baseline / proposed run, returning a single ``RepoProfile``.
``store.read_repo_profile`` gathers the rows and delegates here, so the nesting
logic lives in exactly one place.

Each "row" is any mapping keyed by column name — a ``sqlite3.Row`` or a plain
``dict`` both work. Missing optional columns fall back to sensible defaults so
older databases (predating ``conclusion`` / ``workflow_id``) still assemble.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping, Sequence

from models import Job, RepoProfile, Run, ShadowExperiment, Workflow


def _val(row: Mapping[str, Any], key: str, default: Any = None) -> Any:
    """Read ``key`` from a row mapping, returning ``default`` if absent or null.

    ``sqlite3.Row`` raises ``IndexError`` for unknown columns and has no
    ``.get``; this accessor smooths over both Row and dict so callers do not
    care which they were handed.
    """
    try:
        value = row[key]
    except (KeyError, IndexError):
        return default
    return default if value is None else value


def _job_from_row(row: Mapping[str, Any]) -> Job:
    """Build a ``Job`` from one flat job row."""
    return Job(
        id=_val(row, "id"),
        name=_val(row, "name", ""),
        status=_val(row, "status", ""),
        conclusion=_val(row, "conclusion", ""),
        started_at=_val(row, "started_at", ""),
        duration_s=_val(row, "duration_s", 0.0),
    )


def _workflow_from_row(row: Mapping[str, Any]) -> Workflow:
    """Build a ``Workflow`` from one flat workflow row."""
    return Workflow(
        id=_val(row, "id"),
        filename=_val(row, "filename", ""),
        raw_yaml=_val(row, "raw_yaml", ""),
    )


def _run_from_row(row: Mapping[str, Any], jobs: list[Job]) -> Run:
    """Build a ``Run`` from one flat run row plus its already-built jobs."""
    return Run(
        id=_val(row, "id"),
        gh_run_id=_val(row, "gh_run_id", ""),
        workflow_id=_val(row, "workflow_id", 0),
        status=_val(row, "status", ""),
        created_at=_val(row, "created_at", ""),
        jobs=jobs,
    )


def _experiment_from_row(
    row: Mapping[str, Any], runs_by_id: dict[int, Run]
) -> ShadowExperiment:
    """Build a ``ShadowExperiment``, wiring it to its baseline / proposed runs.

    Raises ``ValueError`` if either referenced run id is missing from the
    already-loaded runs — a dangling experiment is a corrupt graph, not a row to
    silently drop.
    """
    baseline_id = _val(row, "baseline_run_id")
    proposed_id = _val(row, "proposed_run_id")
    baseline = runs_by_id.get(baseline_id)
    proposed = runs_by_id.get(proposed_id)
    exp_id = _val(row, "id")
    if baseline is None:
        raise ValueError(
            f"experiment {exp_id}: baseline_run_id {baseline_id} not found in runs"
        )
    if proposed is None:
        raise ValueError(
            f"experiment {exp_id}: proposed_run_id {proposed_id} not found in runs"
        )
    return ShadowExperiment(
        id=exp_id,
        hypothesis=_val(row, "hypothesis", ""),
        baseline_run=baseline,
        proposed_run=proposed,
        baseline_duration_s=_val(row, "baseline_duration_s", 0.0),
        proposed_duration_s=_val(row, "proposed_duration_s", 0.0),
        outcome=_val(row, "outcome"),
    )


def assemble_repo_profile(
    repo: Mapping[str, Any],
    workflows: Sequence[Mapping[str, Any]],
    runs: Sequence[Mapping[str, Any]],
    jobs: Sequence[Mapping[str, Any]],
    experiments: Sequence[Mapping[str, Any]],
) -> RepoProfile:
    """Nest flat rows into a single ``RepoProfile``.

    ``repo`` is one row; the rest are sequences of rows. Jobs are grouped under
    their ``run_id`` and runs under the repo; experiments are wired to the runs
    by id. Raises ``ValueError`` if an experiment references a run id that is not
    present in ``runs``.
    """
    jobs_by_run: dict[int, list[Job]] = defaultdict(list)
    for job_row in jobs:
        jobs_by_run[_val(job_row, "run_id")].append(_job_from_row(job_row))

    runs_by_id: dict[int, Run] = {}
    for run_row in runs:
        run_id = _val(run_row, "id")
        runs_by_id[run_id] = _run_from_row(run_row, jobs_by_run.get(run_id, []))

    built_experiments = [_experiment_from_row(e, runs_by_id) for e in experiments]

    return RepoProfile(
        id=_val(repo, "id"),
        owner=_val(repo, "owner", ""),
        name=_val(repo, "name", ""),
        workflows=[_workflow_from_row(w) for w in workflows],
        runs=list(runs_by_id.values()),
        experiments=built_experiments,
    )
