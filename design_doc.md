# Model reference

Quick reference for the Pydantic models in `src/models/__init__.py`. See
`CLAUDE.md` for how they fit into the pipeline.

## Job

- `id` — GitHub's job id
- `name` — job name
- `status` — lifecycle state (`queued` / `in_progress` / `completed`)
- `conclusion` — outcome once finished (`success` / `failure` / `skipped` /
  `cancelled`); distinct from `status`, which only tracks *where* the job is
  in its lifecycle
- `started_at` — start time
- `duration_s` — how long the job took, in seconds

## Run

- `id` — internal primary key
- `gh_run_id` — GitHub's run id (kept separate since GitHub's id and our
  storage id aren't guaranteed to be interchangeable everywhere)
- `workflow_id` — id of the parent `Workflow`
- `created_at` — when the run was created
- `jobs` — list of `Job`

## Workflow

- `id` — synthesized as `crc32(path)` (the contents API gives no integer id)
- `filename`
- `raw_yaml`

## RepoProfile

- `id`
- `owner`
- `name`
- `workflows: list[Workflow]`
- `runs: list[Run]`

Access pattern: `profile.runs[0].jobs[0]` gets the first job of the first run.
