"""Tests for assemble_repo_profile — nesting flat rows into a RepoProfile."""

import pytest

from models.assemble import assemble_repo_profile

REPO = {"id": 1, "owner": "octocat", "name": "hello"}
WORKFLOWS = [{"id": 7, "filename": "ci.yml", "raw_yaml": "name: CI\n"}]
RUNS = [
    {
        "id": 10,
        "gh_run_id": "gh-10",
        "workflow_id": 7,
        "status": "completed",
        "created_at": "2024-01-01T00:00:00",
    },
    {
        "id": 20,
        "gh_run_id": "gh-20",
        "workflow_id": 7,
        "status": "completed",
        "created_at": "2024-01-02T00:00:00",
    },
]
JOBS = [
    {
        "id": 100,
        "run_id": 10,
        "name": "build",
        "status": "completed",
        "conclusion": "success",
        "started_at": "2024-01-01T00:01:00",
        "duration_s": 60.0,
    },
    {
        "id": 101,
        "run_id": 10,
        "name": "test",
        "status": "completed",
        "conclusion": "failure",
        "started_at": "2024-01-01T00:02:00",
        "duration_s": 120.0,
    },
]


def test_nests_jobs_under_runs():
    profile = assemble_repo_profile(REPO, WORKFLOWS, RUNS, JOBS, [])
    assert profile.owner == "octocat"
    assert len(profile.workflows) == 1
    by_id = {run.id: run for run in profile.runs}
    assert {job.name for job in by_id[10].jobs} == {"build", "test"}
    assert by_id[20].jobs == []


def test_preserves_job_fields():
    profile = assemble_repo_profile(REPO, WORKFLOWS, RUNS, JOBS, [])
    run = next(r for r in profile.runs if r.id == 10)
    test_job = next(j for j in run.jobs if j.name == "test")
    assert test_job.conclusion == "failure"
    assert test_job.duration_s == 120.0


def test_wires_experiment_to_runs():
    experiments = [
        {
            "id": 1,
            "hypothesis": "faster",
            "baseline_run_id": 10,
            "proposed_run_id": 20,
            "baseline_duration_s": 180.0,
            "proposed_duration_s": 45.0,
            "outcome": "speedup",
        }
    ]
    profile = assemble_repo_profile(REPO, WORKFLOWS, RUNS, JOBS, experiments)
    assert len(profile.experiments) == 1
    exp = profile.experiments[0]
    assert exp.baseline_run.id == 10
    assert exp.proposed_run.id == 20
    assert exp.outcome == "speedup"


def test_missing_run_id_raises():
    experiments = [
        {
            "id": 1,
            "hypothesis": "faster",
            "baseline_run_id": 10,
            "proposed_run_id": 999,
            "baseline_duration_s": 1.0,
            "proposed_duration_s": 1.0,
            "outcome": None,
        }
    ]
    with pytest.raises(ValueError, match="proposed_run_id 999 not found"):
        assemble_repo_profile(REPO, WORKFLOWS, RUNS, JOBS, experiments)


def test_optional_columns_default():
    # A run row missing conclusion/workflow_id (older DB) still assembles.
    runs = [
        {
            "id": 30,
            "gh_run_id": "gh-30",
            "status": "completed",
            "created_at": "2024-01-03T00:00:00",
        }
    ]
    jobs = [
        {
            "id": 200,
            "run_id": 30,
            "name": "lint",
            "status": "completed",
            "started_at": "2024-01-03T00:01:00",
            "duration_s": 5.0,
        }
    ]
    profile = assemble_repo_profile(REPO, [], runs, jobs, [])
    run = profile.runs[0]
    assert run.workflow_id == 0
    assert run.jobs[0].conclusion == ""
