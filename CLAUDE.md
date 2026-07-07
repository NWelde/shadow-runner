# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
uv sync                              # install deps (Python >=3.11)
uv run pytest                        # run all tests
uv run pytest tests/test_store.py    # single file
uv run pytest tests/test_github.py::test_name   # single test
uv run ruff check src tests          # lint (line-length 88)
uv run ruff format src tests         # format

uv run python src/ingest/github.py   # live ingest smoke test (needs GITHUB_TOKEN)
PYTHONPATH=src uv run python triage/run.py   # end-to-end: GitHub -> models -> SQLite -> read back
```

A real `GITHUB_TOKEN` in `.env` (copy `.env.example`) is required for anything that hits GitHub; it's loaded via `python-dotenv` at import of `ingest/github.py` and sent as a bearer token on every request.

## Architecture

The pipeline turns a repo's GitHub Actions history into a persisted, queryable object graph, then derives triage signals from it. Data flows in one direction through four layers:

1. **`src/ingest/github.py`** — synchronous GitHub REST client (`requests`). Returns *raw dicts*, not models. Every call goes through `_request`, which centralizes error handling: non-200 raises `GitHubAPIError` (carries `.status_code`), 403/429 raise `RateLimitError` with a wait hint parsed from rate-limit headers, and 404 returns `None` when `not_found_ok=True`. `fetch_repo_ci_profile` composes workflows + runs and attaches per-run job timing.

2. **`src/pipeline.py`** — the *only* adapter from raw dicts to models (`build_repo_profile`). It computes `duration_s` from `started_at`/`completed_at`, and synthesizes stable workflow IDs as `crc32(path)` since the contents API gives no integer id. GitHub-native integer ids are reused as primary keys everywhere else (run id, job id, repo id).

3. **`src/models/__init__.py`** — frozen (immutable) Pydantic v2 models. `RepoProfile` is the root aggregate: `owner`/`name` + lists of `Workflow`, `Run` (each holding `Job`s), and `ShadowExperiment`. This is the single object passed between layers.

4. **`src/store.py`** — SQLite persistence for the whole `RepoProfile` graph. `write_repo_profile` writes in one transaction in FK order (repos → workflows/runs → jobs → experiments) using `INSERT OR REPLACE`, so re-ingesting the same repo is idempotent. `read_repo_profile` reassembles the graph with batched queries (jobs for all runs fetched in one `IN` query) and rewires experiments to their already-loaded baseline/proposed runs.

**Triage layer** (`src/triage/repo_profile_input.py`) consumes a `RepoProfile` read back from the DB and derives the agent-facing input. `build_triage_input(profile)` is the assembly point, combining four signals into one dict: `build_dep_graph` (parses a workflow's `raw_yaml` into a `{job: [needs]}` adjacency map), `read_av_job_duration` (per-job mean seconds), `read_failure_rate` (per-job % ), and `chain_of_deps` (the **duration-weighted** critical path — the chain with the greatest total time, not the most hops). Note the dependency graph keys come from YAML job *ids* while durations/failure-rates key off the run's job *name*; these are assumed to match.

## Conventions & gotchas

- **Imports are absolute, rooted at `src`** (e.g. `from models import ...`, `from store import ...`). Tests get this via `pythonpath = ["src"]` in `pyproject.toml`; standalone scripts must set `PYTHONPATH=src`. `src/triage/` is **not** a package (no `__init__.py`) — do not use relative imports there.
- The hatch wheel only packages `src/ingest` and `src/models`; `store`, `pipeline`, and `triage` are run-from-source modules, not part of the installable package.
- **Tests use no HTTP mocking library.** `ingest/github.py` exposes a seam via `_get_session()`; tests monkeypatch it to inject a `FakeSession` that routes through a handler returning real `requests.Response` objects. Store tests use an in-memory SQLite fixture. Follow these patterns rather than adding mock dependencies.
