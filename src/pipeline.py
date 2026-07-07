"""Converts raw GitHub API output into Pydantic models ready for storage."""

from __future__ import annotations

import zlib
from datetime import datetime

from ingest.github import fetch_repo_ci_profile, fetch_repo_info
from models import Job, RepoProfile, Run, Workflow


def _duration_s(started_at: str | None, completed_at: str | None) -> float:
    if not started_at or not completed_at:
        return 0.0
    try:
        start = datetime.fromisoformat(started_at)
        end = datetime.fromisoformat(completed_at)
        return max(0.0, (end - start).total_seconds())
    except ValueError:
        return 0.0


def _workflow_id(path: str) -> int:
    # crc32 of the file path gives a stable positive integer with no extra API call.
    return zlib.crc32(path.encode()) & 0x7FFFFFFF


def build_repo_profile(owner: str, repo: str) -> RepoProfile:
    """Fetch a repo's CI data from GitHub and return a fully assembled RepoProfile."""
    repo_info = fetch_repo_info(owner, repo)
    ci = fetch_repo_ci_profile(owner, repo)

    workflows = [
        Workflow(
            id=_workflow_id(wf["path"]),
            filename=wf["name"],
            raw_yaml=wf["raw_yaml"],
        )
        for wf in ci["workflows"]
    ]

    runs: list[Run] = []
    for raw_run in ci["runs"]:
        jobs = [
            Job(
                id=job["id"],
                name=job["name"] or "",
                status=job["status"] or "",
                conclusion=job.get("conclusion") or "",
                started_at=job["started_at"] or "",
                duration_s=_duration_s(job["started_at"], job["completed_at"]),
            )
            for job in raw_run.get("jobs", [])
            if job.get("id") is not None
        ]
        run_path = raw_run.get("path")
        runs.append(
            Run(
                id=raw_run["id"],
                gh_run_id=str(raw_run["id"]),
                workflow_id=_workflow_id(run_path) if run_path else 0,
                status=raw_run.get("status") or "",
                created_at=raw_run.get("created_at") or "",
                jobs=jobs,
            )
        )

    return RepoProfile(
        id=repo_info["id"],
        owner=repo_info["owner"],
        name=repo_info["name"],
        workflows=workflows,
        runs=runs,
        experiments=[],
    )
