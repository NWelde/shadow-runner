# shadow-runner

A synchronous GitHub REST API ingestion layer for CI data — workflow
definitions, run history, per-job timing, and job logs.

## Setup

Requires Python 3.11 and [`uv`](https://docs.astral.sh/uv/).

```bash
uv sync
cp .env.example .env   # then paste your token into .env
```

Create a GitHub token (a fine-grained or classic PAT with read access to the
repos you want) and set it in `.env`:

```
GITHUB_TOKEN=ghp_your_real_token
```

## Usage

```python
from ingest import github

profile = github.fetch_repo_ci_profile("octocat", "hello-world")
# {"workflows": [...], "runs": [...]}  # each run has a "jobs" list
```

### Functions

| Function | Endpoint |
|---|---|
| `fetch_workflows(owner, repo)` | `GET /repos/{o}/{r}/contents/.github/workflows` — fetches, base64-decodes, and YAML-parses each file; `[]` if the directory is absent |
| `fetch_run_history(owner, repo, n=30)` | `GET /repos/{o}/{r}/actions/runs` — last `n` runs as raw dicts |
| `fetch_job_timing(owner, repo, run_id)` | `GET /repos/{o}/{r}/actions/runs/{run_id}/jobs` — name/status/started_at/completed_at per job |
| `fetch_job_logs(owner, repo, job_id)` | `GET /repos/{o}/{r}/actions/jobs/{job_id}/logs` — raw log text |
| `fetch_repo_ci_profile(owner, repo)` | composes workflows + runs (with per-run job timing) |

Non-200 responses raise `GitHubAPIError` (carrying `.status_code`); `403`/`429`
raise `RateLimitError` with a wait hint pulled from the rate-limit headers.

## Quick check

Runs against a real repo (set `GITHUB_TOKEN` first):

```bash
uv run python src/ingest/github.py
```

## Tests

```bash
uv run pytest
```
