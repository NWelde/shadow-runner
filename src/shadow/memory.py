"""Persist and recall shadow-experiment outcomes.

After an experiment is measured, ``record_outcome`` writes the final timings and
verdict back to its row. ``load_past_experiments`` reads a repo's history so the
agent can avoid proposing a change it has already tried.
"""

from __future__ import annotations

import sqlite3

from store import repo_id_for, update_experiment_result


def record_outcome(
    conn: sqlite3.Connection, experiment_id: int, comparison: dict
) -> None:
    """Write a comparison's timings and verdict onto its experiment row.

    ``comparison`` is the dict returned by ``observe.compare_runs``; its
    ``verdict`` becomes the stored ``outcome``.
    """
    update_experiment_result(
        conn,
        experiment_id,
        comparison["baseline_duration_s"],
        comparison["proposed_duration_s"],
        comparison["verdict"],
    )


def load_past_experiments(
    conn: sqlite3.Connection, owner: str, repo: str
) -> list[dict]:
    """Return a repo's experiments as row dicts, most recent first.

    Empty list if the repo has never been ingested. Used to skip re-proposing a
    change that has already been tested.
    """
    repo_id = repo_id_for(conn, owner, repo)
    if repo_id is None:
        return []
    rows = conn.execute(
        "SELECT * FROM shadow_experiments WHERE repo_id = ? ORDER BY id DESC",
        (repo_id,),
    ).fetchall()
    return [dict(row) for row in rows]
