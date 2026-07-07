"""Synchronous GitHub REST API ingestion layer.

Pulls workflow definitions, run history, job timing, and job logs for a repo
using ``requests``. A ``GITHUB_TOKEN`` is loaded from ``.env`` via
``python-dotenv`` and sent as a bearer token on every request.
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any, Optional

import requests
import yaml
from dotenv import load_dotenv

load_dotenv()

API_BASE = "https://api.github.com"
API_VERSION = "2022-11-28"
TIMEOUT = 30.0


class GitHubAPIError(Exception):
    """Raised when the GitHub API returns a non-success status code."""

    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(f"GitHub API error {status_code}: {message}")


class RateLimitError(GitHubAPIError):
    """Raised when the API reports a primary or secondary rate limit (403/429)."""


def _token() -> Optional[str]:
    return os.environ.get("GITHUB_TOKEN")


def _auth_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": API_VERSION,
    }
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _get_session() -> requests.Session:
    """Build the HTTP session. Tests monkeypatch this to inject a fake session."""
    session = requests.Session()
    session.headers.update(_auth_headers())
    return session


def _rate_limit_message(response: requests.Response) -> str:
    """Build a 'wait N seconds' message from rate-limit headers, if present."""
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        return f"Rate limited. Retry after {retry_after} seconds."

    reset = response.headers.get("X-RateLimit-Reset")
    remaining = response.headers.get("X-RateLimit-Remaining")
    if reset is not None and remaining == "0":
        import time

        wait = max(0, int(reset) - int(time.time()))
        return f"Rate limit exhausted. Reset in {wait} seconds."

    return "Rate limited. No retry hint provided by the API."


def _request(
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    not_found_ok: bool = False,
) -> Optional[requests.Response]:
    """Make a request and raise on failure.

    Returns the response on success. If ``not_found_ok`` is set and the API
    returns 404, returns ``None`` instead of raising.
    """
    session = _get_session()
    try:
        response = session.request(
            method, f"{API_BASE}{path}", params=params, timeout=TIMEOUT
        )
    finally:
        session.close()

    if response.status_code in (403, 429):
        raise RateLimitError(response.status_code, _rate_limit_message(response))

    if response.status_code == 404 and not_found_ok:
        return None

    if response.status_code != 200:
        raise GitHubAPIError(response.status_code, response.text)

    return response


def fetch_workflows(owner: str, repo: str) -> list[dict[str, Any]]:
    """Fetch and parse every workflow file under ``.github/workflows``.

    Returns one dict per workflow file with its ``name``, ``path``, and the
    YAML-parsed ``content``. Returns an empty list if the directory is absent.
    """
    listing = _request(
        "GET",
        f"/repos/{owner}/{repo}/contents/.github/workflows",
        not_found_ok=True,
    )
    if listing is None:
        return []

    workflows: list[dict[str, Any]] = []
    for entry in listing.json():
        if entry.get("type") != "file":
            continue

        file_resp = _request("GET", f"/repos/{owner}/{repo}/contents/{entry['path']}")
        payload = file_resp.json()
        decoded = base64.b64decode(payload["content"]).decode("utf-8")

        try:
            content = yaml.safe_load(decoded)
        except yaml.YAMLError:
            content = {}
        workflows.append(
            {
                "name": entry["name"],
                "path": entry["path"],
                "raw_yaml": decoded,
                "content": content,
            }
        )

    return workflows


def fetch_run_history(owner: str, repo: str, n: int = 30) -> list[dict[str, Any]]:
    """Return the most recent ``n`` workflow runs as raw dicts."""
    response = _request(
        "GET",
        f"/repos/{owner}/{repo}/actions/runs",
        params={"per_page": min(n, 100)},
    )
    return response.json().get("workflow_runs", [])[:n]


def fetch_job_timing(owner: str, repo: str, run_id: int) -> list[dict[str, Any]]:
    """Return per-job timing for a run: name, status, conclusion, started_at, completed_at.

    ``status`` is the lifecycle phase (queued/in_progress/completed); ``conclusion``
    is the outcome (success/failure/cancelled/...) and is what failure rate keys on.
    """
    response = _request("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}/jobs")
    return [
        {
            "id": job.get("id"),
            "name": job.get("name"),
            "status": job.get("status"),
            "conclusion": job.get("conclusion"),
            "started_at": job.get("started_at"),
            "completed_at": job.get("completed_at"),
        }
        for job in response.json().get("jobs", [])
    ]


def fetch_repo_info(owner: str, repo: str) -> dict[str, Any]:
    """Return the repo's integer id, owner login, and name."""
    response = _request("GET", f"/repos/{owner}/{repo}")
    data = response.json()
    return {
        "id": data["id"],
        "owner": data["owner"]["login"],
        "name": data["name"],
    }


def fetch_repo_info_safe(owner: str, repo: str) -> Optional[dict[str, Any]]:
    """Like ``fetch_repo_info`` but returns ``None`` if the repo 404s.

    Used to poll a freshly-created fork until GitHub has finished building it.
    """
    response = _request("GET", f"/repos/{owner}/{repo}", not_found_ok=True)
    if response is None:
        return None
    data = response.json()
    return {"id": data["id"], "owner": data["owner"]["login"], "name": data["name"]}


def fetch_job_logs(owner: str, repo: str, job_id: int) -> str:
    """Return the raw log text for a single job."""
    response = _request("GET", f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs")
    return response.text


def fetch_repo_ci_profile(owner: str, repo: str) -> dict[str, Any]:
    """Compose workflows + run history (with per-run job timing) for a repo."""
    workflows = fetch_workflows(owner, repo)
    runs = fetch_run_history(owner, repo)
    for run in runs:
        run["jobs"] = fetch_job_timing(owner, repo, run["id"])
    return {"workflows": workflows, "runs": runs}


# ---------------------------------------------------------------------------
# Mutation layer — write operations for the shadow-experiment flow.
#
# ``_request`` above is GET/200-centric (it raises on anything that is not 200).
# Write endpoints legitimately return 201/202/204, so they go through the
# separate ``_write_request`` below, which accepts the success codes the GitHub
# API documents per endpoint. ``_request`` is deliberately left untouched.
# ---------------------------------------------------------------------------


def _write_request(
    method: str,
    path: str,
    *,
    json_body: Optional[dict[str, Any]] = None,
    accepted: tuple[int, ...] = (200, 201, 202, 204),
) -> requests.Response:
    """Make a write request, raising unless the status is in ``accepted``."""
    session = _get_session()
    try:
        response = session.request(
            method, f"{API_BASE}{path}", json=json_body, timeout=TIMEOUT
        )
    finally:
        session.close()

    if response.status_code in (403, 429):
        raise RateLimitError(response.status_code, _rate_limit_message(response))
    if response.status_code not in accepted:
        raise GitHubAPIError(response.status_code, response.text)
    return response


def get_authenticated_user(owner_fallback: Optional[str] = None) -> str:
    """Return the login of the token's owner (used as the fork destination)."""
    response = _request("GET", "/user", not_found_ok=True)
    if response is None:
        if owner_fallback is not None:
            return owner_fallback
        raise GitHubAPIError(404, "could not resolve authenticated user")
    return response.json()["login"]


def fork_repo(owner: str, repo: str) -> dict[str, Any]:
    """Fork ``owner/repo`` under the authenticated account; return the fork dict.

    Forking is asynchronous on GitHub's side: a 202 means "accepted, building".
    Callers should poll the fork (e.g. ``fetch_repo_info``) until it is ready.
    """
    response = _write_request("POST", f"/repos/{owner}/{repo}/forks")
    return response.json()


def delete_repo(owner: str, repo: str) -> None:
    """Delete a repository (used to clean up a throwaway fork)."""
    _write_request("DELETE", f"/repos/{owner}/{repo}", accepted=(204,))


def set_actions_enabled(owner: str, repo: str, enabled: bool = True) -> None:
    """Enable (or disable) GitHub Actions on a repo.

    Forks have Actions **disabled by default**, so a ``workflow_dispatch`` would
    fail until this is called on the fork.
    """
    _write_request(
        "PUT",
        f"/repos/{owner}/{repo}/actions/permissions",
        json_body={"enabled": enabled},
        accepted=(204,),
    )


def get_default_branch_sha(owner: str, repo: str) -> tuple[str, str]:
    """Return ``(default_branch, head_commit_sha)`` for a repo."""
    repo_resp = _request("GET", f"/repos/{owner}/{repo}")
    branch = repo_resp.json()["default_branch"]
    ref_resp = _request("GET", f"/repos/{owner}/{repo}/git/ref/heads/{branch}")
    return branch, ref_resp.json()["object"]["sha"]


def create_branch(owner: str, repo: str, branch: str, from_sha: str) -> None:
    """Create ``branch`` pointing at ``from_sha`` (a new git ref)."""
    _write_request(
        "POST",
        f"/repos/{owner}/{repo}/git/refs",
        json_body={"ref": f"refs/heads/{branch}", "sha": from_sha},
        accepted=(201,),
    )


def get_file_sha(owner: str, repo: str, path: str, ref: str) -> Optional[str]:
    """Return the blob sha of ``path`` on ``ref``, or ``None`` if it is absent."""
    response = _request(
        "GET",
        f"/repos/{owner}/{repo}/contents/{path}",
        params={"ref": ref},
        not_found_ok=True,
    )
    return None if response is None else response.json().get("sha")


def put_file(
    owner: str,
    repo: str,
    path: str,
    content: str,
    message: str,
    branch: str,
) -> dict[str, Any]:
    """Create or update ``path`` on ``branch`` with ``content`` (a commit)."""
    body: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    existing = get_file_sha(owner, repo, path, branch)
    if existing is not None:
        body["sha"] = existing
    response = _write_request(
        "PUT",
        f"/repos/{owner}/{repo}/contents/{path}",
        json_body=body,
        accepted=(200, 201),
    )
    return response.json()


def get_workflow_id(owner: str, repo: str, filename: str) -> int:
    """Return the integer workflow id whose file basename matches ``filename``."""
    response = _request("GET", f"/repos/{owner}/{repo}/actions/workflows")
    for workflow in response.json().get("workflows", []):
        if workflow.get("path", "").endswith(f"/{filename}"):
            return workflow["id"]
    raise GitHubAPIError(404, f"workflow {filename!r} not found in {owner}/{repo}")


def trigger_workflow_dispatch(
    owner: str, repo: str, workflow_id: int, ref: str
) -> None:
    """Trigger a ``workflow_dispatch`` run of ``workflow_id`` on ``ref``."""
    _write_request(
        "POST",
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches",
        json_body={"ref": ref},
        accepted=(204,),
    )


def get_run(owner: str, repo: str, run_id: str) -> dict[str, Any]:
    """Return the full run dict for a single workflow run id."""
    response = _request("GET", f"/repos/{owner}/{repo}/actions/runs/{run_id}")
    return response.json()


def find_latest_run_for_branch(
    owner: str, repo: str, workflow_id: int, branch: str
) -> Optional[dict[str, Any]]:
    """Return the most recent run of ``workflow_id`` on ``branch`` (or ``None``).

    After a dispatch, GitHub takes a moment to register the run; callers poll
    this until it returns a dict to capture the freshly-created run id.
    """
    response = _request(
        "GET",
        f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs",
        params={"branch": branch, "event": "workflow_dispatch", "per_page": 1},
    )
    runs = response.json().get("workflow_runs", [])
    return runs[0] if runs else None


if __name__ == "__main__":
    profile = fetch_repo_ci_profile("torvalds", "linux")
    print(json.dumps(profile, indent=2, default=str))
