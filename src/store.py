"""SQLite persistence layer — reads and writes for RepoProfile objects.

Write path: ``write_repo_profile`` persists the full object graph in one
transaction, respecting FK order: repos → workflows / runs → jobs → experiments.

Read path: ``read_repo_profile`` reassembles a full ``RepoProfile`` from the
five tables using two round-trips (one per table, jobs batched with IN).
``list_repos`` returns a lightweight summary of every stored repo.
"""

import sqlite3
import zlib
from datetime import datetime, timezone
from typing import Optional

from models import Job, RepoProfile, Run, ShadowExperiment, Workflow
from models.assemble import assemble_repo_profile

_DDL = """
CREATE TABLE IF NOT EXISTS repos (
    id         INTEGER PRIMARY KEY,
    owner      TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    fetched_at TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS workflows (
    id       INTEGER PRIMARY KEY,
    repo_id  INTEGER NOT NULL REFERENCES repos(id),
    filename TEXT    NOT NULL,
    raw_yaml TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY,
    repo_id     INTEGER NOT NULL REFERENCES repos(id),
    gh_run_id   TEXT    NOT NULL,
    workflow_id INTEGER NOT NULL DEFAULT 0,
    status      TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id         INTEGER PRIMARY KEY,
    run_id     INTEGER NOT NULL REFERENCES runs(id),
    name       TEXT    NOT NULL,
    status     TEXT    NOT NULL,
    conclusion TEXT    NOT NULL DEFAULT '',
    started_at TEXT    NOT NULL,
    duration_s REAL    NOT NULL
);

CREATE TABLE IF NOT EXISTS shadow_experiments (
    id                  INTEGER PRIMARY KEY,
    repo_id             INTEGER NOT NULL REFERENCES repos(id),
    hypothesis          TEXT    NOT NULL,
    baseline_run_id     INTEGER NOT NULL REFERENCES runs(id),
    proposed_run_id     INTEGER NOT NULL REFERENCES runs(id),
    baseline_duration_s REAL    NOT NULL,
    proposed_duration_s REAL    NOT NULL,
    outcome             TEXT
);
"""


def connect(path: str = "shadow_runner.db") -> sqlite3.Connection:
    """Open a connection with foreign keys enforced and Row factory set."""
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create all tables. Safe to call on an already-initialised database."""
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(_DDL)
    # Migrate pre-existing tables that predate added columns. CREATE TABLE IF NOT
    # EXISTS won't add columns to a table that already exists.
    job_cols = {row["name"] for row in conn.execute("PRAGMA table_info(jobs)")}
    if "conclusion" not in job_cols:
        conn.execute("ALTER TABLE jobs ADD COLUMN conclusion TEXT NOT NULL DEFAULT ''")
    run_cols = {row["name"] for row in conn.execute("PRAGMA table_info(runs)")}
    if "workflow_id" not in run_cols:
        conn.execute(
            "ALTER TABLE runs ADD COLUMN workflow_id INTEGER NOT NULL DEFAULT 0"
        )


def _write_workflow(conn: sqlite3.Connection, workflow: Workflow, repo_id: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO workflows (id, repo_id, filename, raw_yaml)"
        " VALUES (?, ?, ?, ?)",
        (workflow.id, repo_id, workflow.filename, workflow.raw_yaml),
    )


def _write_job(conn: sqlite3.Connection, job: Job, run_id: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO jobs"
        " (id, run_id, name, status, conclusion, started_at, duration_s)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            job.id,
            run_id,
            job.name,
            job.status,
            job.conclusion,
            job.started_at,
            job.duration_s,
        ),
    )


def _write_run(conn: sqlite3.Connection, run: Run, repo_id: int) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO runs"
        " (id, repo_id, gh_run_id, workflow_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (
            run.id,
            repo_id,
            run.gh_run_id,
            run.workflow_id,
            run.status,
            run.created_at,
        ),
    )
    for job in run.jobs:
        _write_job(conn, job, run.id)


def _write_experiment(
    conn: sqlite3.Connection, exp: ShadowExperiment, repo_id: int
) -> None:
    conn.execute(
        "INSERT OR REPLACE INTO shadow_experiments"
        " (id, repo_id, hypothesis, baseline_run_id, proposed_run_id,"
        "  baseline_duration_s, proposed_duration_s, outcome)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            exp.id,
            repo_id,
            exp.hypothesis,
            exp.baseline_run.id,
            exp.proposed_run.id,
            exp.baseline_duration_s,
            exp.proposed_duration_s,
            exp.outcome,
        ),
    )


def write_repo_profile(
    conn: sqlite3.Connection,
    profile: RepoProfile,
    fetched_at: Optional[str] = None,
) -> None:
    """Persist a full RepoProfile in a single transaction.

    Re-calling with the same data is safe (INSERT OR REPLACE). ``fetched_at``
    defaults to the current UTC time if omitted.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()

    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO repos (id, owner, name, fetched_at)"
            " VALUES (?, ?, ?, ?)",
            (profile.id, profile.owner, profile.name, fetched_at),
        )
        for workflow in profile.workflows:
            _write_workflow(conn, workflow, profile.id)
        for run in profile.runs:
            _write_run(conn, run, profile.id)
        for exp in profile.experiments:
            _write_experiment(conn, exp, profile.id)


# ---------------------------------------------------------------------------
# Read layer
# ---------------------------------------------------------------------------


def list_repos(conn: sqlite3.Connection) -> list[dict]:
    """Return id, owner, name, fetched_at for every repo in the database."""
    rows = conn.execute(
        "SELECT id, owner, name, fetched_at FROM repos ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def _jobs_for_runs(conn: sqlite3.Connection, run_ids: list[int]) -> list[sqlite3.Row]:
    """Fetch all job rows for the given run ids in one batched ``IN`` query."""
    if not run_ids:
        return []
    placeholders = ",".join("?" * len(run_ids))
    return conn.execute(
        "SELECT id, run_id, name, status, conclusion, started_at, duration_s"
        f" FROM jobs WHERE run_id IN ({placeholders})",
        run_ids,
    ).fetchall()


def read_repo_profile(conn: sqlite3.Connection, repo_id: int) -> RepoProfile:
    """Reassemble a full RepoProfile from the database.

    Reads the five flat row sets for ``repo_id`` with batched queries (jobs for
    all runs fetched in one ``IN`` query) and hands them to
    ``assemble_repo_profile`` for nesting. Raises ``ValueError`` if ``repo_id``
    does not exist, or if an experiment references a run absent from the repo.
    """
    repo_row = conn.execute(
        "SELECT id, owner, name FROM repos WHERE id = ?", (repo_id,)
    ).fetchone()
    if repo_row is None:
        raise ValueError(f"repo {repo_id} not found")

    workflow_rows = conn.execute(
        "SELECT id, filename, raw_yaml FROM workflows WHERE repo_id = ?",
        (repo_id,),
    ).fetchall()
    run_rows = conn.execute(
        "SELECT id, gh_run_id, workflow_id, status, created_at"
        " FROM runs WHERE repo_id = ?",
        (repo_id,),
    ).fetchall()
    job_rows = _jobs_for_runs(conn, [r["id"] for r in run_rows])
    experiment_rows = conn.execute(
        "SELECT id, hypothesis, baseline_run_id, proposed_run_id,"
        " baseline_duration_s, proposed_duration_s, outcome"
        " FROM shadow_experiments WHERE repo_id = ?",
        (repo_id,),
    ).fetchall()

    return assemble_repo_profile(
        repo_row, workflow_rows, run_rows, job_rows, experiment_rows
    )


def repo_id_for(conn: sqlite3.Connection, owner: str, name: str) -> Optional[int]:
    """Return the stored repo id for ``owner/name``, or ``None`` if absent."""
    row = conn.execute(
        "SELECT id FROM repos WHERE owner = ? AND name = ?", (owner, name)
    ).fetchone()
    return row["id"] if row else None


def load_repo_profile(conn: sqlite3.Connection, owner: str, repo: str) -> RepoProfile:
    """Load a RepoProfile by ``owner``/``repo`` name (resolves the id first).

    Raises ``ValueError`` if the repo has not been ingested.
    """
    repo_id = repo_id_for(conn, owner, repo)
    if repo_id is None:
        raise ValueError(f"repo {owner}/{repo} not found — run `ingest` first")
    return read_repo_profile(conn, repo_id)


# ---------------------------------------------------------------------------
# Granular row writers — used by the shadow-experiment flow, where rows are
# created one at a time as live GitHub Actions runs are triggered and observed
# (as opposed to ``write_repo_profile``, which persists a whole graph at once).
# ---------------------------------------------------------------------------


def save_repo(
    conn: sqlite3.Connection,
    owner: str,
    name: str,
    repo_id: Optional[int] = None,
    fetched_at: Optional[str] = None,
) -> int:
    """Insert (or replace) a repo row and return its id.

    ``repo_id`` is GitHub's integer id when known (reused as the primary key, as
    everywhere else); omit it to let SQLite autoincrement one.
    """
    if fetched_at is None:
        fetched_at = datetime.now(timezone.utc).isoformat()
    if repo_id is None:
        cur = conn.execute(
            "INSERT INTO repos (owner, name, fetched_at) VALUES (?, ?, ?)",
            (owner, name, fetched_at),
        )
        new_id = cur.lastrowid
    else:
        conn.execute(
            "INSERT OR REPLACE INTO repos (id, owner, name, fetched_at)"
            " VALUES (?, ?, ?, ?)",
            (repo_id, owner, name, fetched_at),
        )
        new_id = repo_id
    conn.commit()
    return new_id


def save_workflow(
    conn: sqlite3.Connection, repo_id: int, filename: str, raw_yaml: str
) -> int:
    """Insert (or replace) a workflow row and return its id.

    The id is ``crc32(filename)`` — a stable positive integer, matching how
    ``pipeline`` synthesises workflow ids (the contents API gives no integer id).
    """
    workflow_id = zlib.crc32(filename.encode()) & 0x7FFFFFFF
    conn.execute(
        "INSERT OR REPLACE INTO workflows (id, repo_id, filename, raw_yaml)"
        " VALUES (?, ?, ?, ?)",
        (workflow_id, repo_id, filename, raw_yaml),
    )
    conn.commit()
    return workflow_id


def save_run(
    conn: sqlite3.Connection,
    repo_id: int,
    gh_run_id: str,
    status: str,
    created_at: str,
    workflow_id: int = 0,
) -> int:
    """Insert a run row (autoincrement id) and return its new id."""
    cur = conn.execute(
        "INSERT INTO runs (repo_id, gh_run_id, workflow_id, status, created_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (repo_id, gh_run_id, workflow_id, status, created_at),
    )
    conn.commit()
    return cur.lastrowid


def save_job(
    conn: sqlite3.Connection,
    run_id: int,
    name: str,
    status: str,
    started_at: str,
    duration_s: float,
    conclusion: str = "",
) -> int:
    """Insert a job row (autoincrement id) and return its new id."""
    cur = conn.execute(
        "INSERT INTO jobs (run_id, name, status, conclusion, started_at, duration_s)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (run_id, name, status, conclusion, started_at, duration_s),
    )
    conn.commit()
    return cur.lastrowid


def save_experiment(
    conn: sqlite3.Connection,
    repo_id: int,
    hypothesis: str,
    baseline_run_id: int,
    proposed_run_id: int,
    baseline_duration_s: float = 0.0,
    proposed_duration_s: float = 0.0,
    outcome: Optional[str] = None,
) -> int:
    """Insert a shadow_experiment row (autoincrement id) and return its new id.

    Durations and outcome default to placeholders; ``update_experiment_result``
    fills them in once both runs complete and are compared.
    """
    cur = conn.execute(
        "INSERT INTO shadow_experiments (repo_id, hypothesis, baseline_run_id,"
        " proposed_run_id, baseline_duration_s, proposed_duration_s, outcome)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            repo_id,
            hypothesis,
            baseline_run_id,
            proposed_run_id,
            baseline_duration_s,
            proposed_duration_s,
            outcome,
        ),
    )
    conn.commit()
    return cur.lastrowid


def update_experiment_result(
    conn: sqlite3.Connection,
    experiment_id: int,
    baseline_duration_s: float,
    proposed_duration_s: float,
    outcome: str,
) -> None:
    """Record the measured timings and verdict on an existing experiment row."""
    conn.execute(
        "UPDATE shadow_experiments SET baseline_duration_s = ?,"
        " proposed_duration_s = ?, outcome = ? WHERE id = ?",
        (baseline_duration_s, proposed_duration_s, outcome, experiment_id),
    )
    conn.commit()
