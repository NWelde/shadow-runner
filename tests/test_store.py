import sqlite3

import pytest

from models import Job, RepoProfile, Run, ShadowExperiment, Workflow
from store import init_db, list_repos, read_repo_profile, write_repo_profile


@pytest.fixture
def conn():
    db = sqlite3.connect(":memory:")
    db.execute("PRAGMA foreign_keys = ON")
    db.row_factory = sqlite3.Row
    init_db(db)
    yield db
    db.close()


WORKFLOW = Workflow(id=1, filename="ci.yml", raw_yaml="name: CI\n")

JOB_A = Job(
    id=100,
    name="build",
    status="success",
    started_at="2024-01-01T00:01:00",
    duration_s=60.0,
)
JOB_B = Job(
    id=101,
    name="test",
    status="success",
    started_at="2024-01-01T00:02:00",
    duration_s=120.0,
)

RUN_1 = Run(
    id=10,
    gh_run_id="gh-10",
    status="completed",
    created_at="2024-01-01T00:00:00",
    jobs=[JOB_A, JOB_B],
)
RUN_2 = Run(
    id=20,
    gh_run_id="gh-20",
    status="completed",
    created_at="2024-01-02T00:00:00",
    jobs=[],
)

PROFILE = RepoProfile(
    id=1,
    owner="octocat",
    name="hello",
    workflows=[WORKFLOW],
    runs=[RUN_1, RUN_2],
    experiments=[],
)


def test_write_repo_row(conn):
    write_repo_profile(conn, PROFILE)
    row = conn.execute("SELECT * FROM repos WHERE id = 1").fetchone()
    assert row["owner"] == "octocat"
    assert row["name"] == "hello"
    assert row["fetched_at"] is not None


def test_write_workflows(conn):
    write_repo_profile(conn, PROFILE)
    rows = conn.execute("SELECT * FROM workflows WHERE repo_id = 1").fetchall()
    assert len(rows) == 1
    assert rows[0]["filename"] == "ci.yml"
    assert rows[0]["raw_yaml"] == "name: CI\n"


def test_write_runs(conn):
    write_repo_profile(conn, PROFILE)
    rows = conn.execute("SELECT id FROM runs WHERE repo_id = 1").fetchall()
    assert {r["id"] for r in rows} == {10, 20}


def test_write_jobs_attached_to_run(conn):
    write_repo_profile(conn, PROFILE)
    rows = conn.execute("SELECT * FROM jobs WHERE run_id = 10").fetchall()
    assert len(rows) == 2
    assert {r["name"] for r in rows} == {"build", "test"}
    assert (
        conn.execute("SELECT COUNT(*) FROM jobs WHERE run_id = 20").fetchone()[0] == 0
    )


def test_write_is_idempotent(conn):
    write_repo_profile(conn, PROFILE)
    write_repo_profile(conn, PROFILE)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2
    assert conn.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 2


def test_write_experiment(conn):
    exp = ShadowExperiment(
        id=1,
        hypothesis="faster build",
        baseline_run=RUN_1,
        proposed_run=RUN_2,
        baseline_duration_s=180.0,
        proposed_duration_s=45.0,
        outcome=None,
    )
    profile = RepoProfile(
        id=1,
        owner="octocat",
        name="hello",
        workflows=[],
        runs=[RUN_1, RUN_2],
        experiments=[exp],
    )
    write_repo_profile(conn, profile)
    row = conn.execute("SELECT * FROM shadow_experiments WHERE id = 1").fetchone()
    assert row["hypothesis"] == "faster build"
    assert row["baseline_run_id"] == 10
    assert row["proposed_run_id"] == 20
    assert row["outcome"] is None


def test_fetched_at_custom_value(conn):
    write_repo_profile(conn, PROFILE, fetched_at="2024-06-01T12:00:00")
    row = conn.execute("SELECT fetched_at FROM repos WHERE id = 1").fetchone()
    assert row["fetched_at"] == "2024-06-01T12:00:00"


# ---------------------------------------------------------------------------
# Read tests
# ---------------------------------------------------------------------------


def test_read_roundtrip(conn):
    write_repo_profile(conn, PROFILE)
    result = read_repo_profile(conn, 1)

    assert result.id == 1
    assert result.owner == "octocat"
    assert result.name == "hello"
    assert len(result.workflows) == 1
    assert result.workflows[0].filename == "ci.yml"
    assert len(result.runs) == 2


def test_read_jobs_attached_to_runs(conn):
    write_repo_profile(conn, PROFILE)
    result = read_repo_profile(conn, 1)

    run_by_id = {r.id: r for r in result.runs}
    assert len(run_by_id[10].jobs) == 2
    assert {j.name for j in run_by_id[10].jobs} == {"build", "test"}
    assert run_by_id[20].jobs == []


def test_read_experiment_wires_runs(conn):
    exp = ShadowExperiment(
        id=1,
        hypothesis="faster build",
        baseline_run=RUN_1,
        proposed_run=RUN_2,
        baseline_duration_s=180.0,
        proposed_duration_s=45.0,
        outcome="improvement",
    )
    profile = RepoProfile(
        id=1,
        owner="octocat",
        name="hello",
        workflows=[],
        runs=[RUN_1, RUN_2],
        experiments=[exp],
    )
    write_repo_profile(conn, profile)
    result = read_repo_profile(conn, 1)

    assert len(result.experiments) == 1
    assert result.experiments[0].baseline_run.id == 10
    assert result.experiments[0].proposed_run.id == 20
    assert result.experiments[0].outcome == "improvement"


def test_read_nonexistent_repo_raises(conn):
    with pytest.raises(ValueError, match="repo 999 not found"):
        read_repo_profile(conn, 999)


def test_list_repos(conn):
    write_repo_profile(conn, PROFILE, fetched_at="2024-06-01T00:00:00")
    second = RepoProfile(
        id=2,
        owner="octocat",
        name="world",
        workflows=[],
        runs=[],
        experiments=[],
    )
    write_repo_profile(conn, second)
    repos = list_repos(conn)

    assert len(repos) == 2
    assert repos[0] == {
        "id": 1,
        "owner": "octocat",
        "name": "hello",
        "fetched_at": "2024-06-01T00:00:00",
    }
    assert repos[1]["name"] == "world"


def test_fk_violation_rolls_back(conn):
    # Write without first inserting the repo — FK on runs.repo_id=2 should fail
    # (repos table has no row with id=2)
    with pytest.raises(Exception):
        write_repo_profile(
            conn,
            RepoProfile(
                id=2, owner="ghost", name="repo", workflows=[], runs=[], experiments=[]
            ),
        )
        # Manually break FK by inserting a run referencing a non-existent repo
        conn.execute("INSERT INTO runs VALUES (99, 999, 'x', 'done', '2024-01-01')")
        conn.commit()
