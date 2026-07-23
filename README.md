# CI Shadow Runner

CI Shadow Runner reads a project's GitHub Actions history, finds jobs that
are waiting on each other for no real reason, and proves a fix works by
actually running both versions on real infrastructure and measuring the
difference — before ever proposing a change.

It doesn't say "this should be faster." It says "we ran it, and it *was*
faster, by this many seconds" — then writes a report and a ready-to-paste PR
body with the evidence.

See [`outputs/madeintandem_jsonb_accessor_pr.md`](outputs/madeintandem_jsonb_accessor_pr.md)
for a real example: removing an unnecessary `needs: lint` dependency that was
silently skipping 186 test jobs whenever lint failed.

## Pipeline

```
  1. INGEST          2. TRIAGE            3. SHADOW             4. PITCH
  Read CI config  →  Find slow &      →   Run old vs new    →   Write report
  + run history       unnecessary          for real, measure     + PR body
  from GitHub          waits                the speedup
```

| Stage | Module |
|---|---|
| Ingest | `src/ingest/github.py` — synchronous GitHub REST client |
| Model / persist | `src/pipeline.py`, `src/models/`, `src/store.py` — raw dicts → Pydantic models → SQLite |
| Triage | `src/triage/` — dependency graph, critical path, failure rate |
| Shadow | `src/shadow/` — proposes a change and runs both versions to measure it |
| Pitch | `src/pitch/` — writes the human-readable report and PR body |

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

Four subcommands mirror the pipeline stages:

```bash
uv run python cli.py ingest --repo django/django
uv run python cli.py triage --repo django/django
uv run python cli.py shadow --repo django/django --dry-run
uv run python cli.py pitch  --repo django/django
```

Or use the ingest layer directly:

```python
from ingest import github

profile = github.fetch_repo_ci_profile("octocat", "hello-world")
# {"workflows": [...], "runs": [...]}  # each run has a "jobs" list
```

### Ingest functions

| Function | Endpoint |
|---|---|
| `fetch_workflows(owner, repo)` | `GET /repos/{o}/{r}/contents/.github/workflows` — fetches, base64-decodes, and YAML-parses each file; `[]` if the directory is absent |
| `fetch_run_history(owner, repo, n=30)` | `GET /repos/{o}/{r}/actions/runs` — last `n` runs as raw dicts |
| `fetch_job_timing(owner, repo, run_id)` | `GET /repos/{o}/{r}/actions/runs/{run_id}/jobs` — name/status/started_at/completed_at per job |
| `fetch_job_logs(owner, repo, job_id)` | `GET /repos/{o}/{r}/actions/jobs/{job_id}/logs` — raw log text |
| `fetch_repo_ci_profile(owner, repo)` | composes workflows + runs (with per-run job timing) |

Non-200 responses raise `GitHubAPIError` (carrying `.status_code`); `403`/`429`
raise `RateLimitError` with a wait hint pulled from the rate-limit headers.

## Tests

```bash
uv run pytest
```

## License

[MIT](LICENSE)
