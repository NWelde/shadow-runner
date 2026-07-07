# 05 — Triage

## What "triage" means here

**Triage** is a word borrowed from hospitals: quickly sorting many cases to find
the ones that matter most and deciding what to do about each. In this tool,
triage is the step that looks at all the facts about a pipeline and produces a
short list of **findings** — specific, ranked problems worth acting on.

## What triage reads

Triage does not look at GitHub directly. It reads the data the **ingest** step
already saved into the local database, and from it builds one tidy summary object
(see `build_triage_input` in `src/triage/repo_profile_input.py`). That summary
has five parts:

```python
{
    "dep_graph":   {"lint": [], "test": ["lint"], "build": ["lint"]},
    "avg_durations": {"lint": 42.3, "test": 487.1, "build": 183.4},
    "failure_rates": {"lint": 2.1, "test": 8.4, "build": 0.0},
    "critical_path": ["lint", "test"],
    "critical_path_duration_s": 529.4
}
```

In plain English:

- **dep_graph** — who waits for whom (page 02). `test` waits for `lint`.
- **avg_durations** — how many seconds each job takes on average, across the
  recent runs. `test` averages 487 seconds.
- **failure_rates** — what percentage of recent runs each job failed in. `test`
  fails 8.4% of the time.
- **critical_path** — the slowest unavoidable chain (page 03): lint then test.
- **critical_path_duration_s** — that chain's total time: about 529 seconds.

## What a "finding" is

A **finding** is one structured observation: a single problem, described in a
fixed shape so a computer can act on it. Its fields (see `Finding` in
`src/triage/triage.py`):

- `job` — which job the finding is about.
- `dependency_to_remove` — if the fix is to drop a `needs:` edge, which one
  (otherwise empty).
- `problem` — one sentence describing the issue in human words.
- `type` — one of three categories:
  - `parallelism_opportunity` — a `needs:` that looks unnecessary, so two jobs
    could run side by side.
  - `slow_on_critical_path` — a job that's slow *and* on the critical path, so
    speeding it up would actually help.
  - `high_failure_rate` — a job that fails often, wasting everyone's time.
- `estimated_saving_s` — a rough guess at seconds saved if the fix works.
- `confidence` — `high`, `medium`, or `low`.

## How the model decides what to flag

The summary object is handed to a small AI model (`gemma4:4b`) running locally
through Ollama. The model is told, in effect: "Here is the dependency graph,
durations, failure rates, and critical path. Find the highest-impact problems and
return them as a JSON list of findings, nothing else."

It reasons roughly the way the earlier pages do: a slow job that sits on the
critical path is a candidate to speed up or parallelise; two jobs that share a
dependency but don't depend on each other are a candidate to run together; a job
that fails often is a candidate to investigate. The tool then validates every
item the model returns against the strict `Finding` shape, and retries once with a
stricter prompt if the model's output isn't clean JSON. If it still can't parse
the output, it raises a `TriageError` rather than guessing.

## A real example finding

```json
{
  "job": "build",
  "dependency_to_remove": "test",
  "problem": "build waits for test, but build only needs the source code, not the test results, so the wait is unnecessary.",
  "type": "parallelism_opportunity",
  "estimated_saving_s": 180.0,
  "confidence": "medium"
}
```

This says: *build is waiting for test for no good reason; drop that `needs: test`
and the two can run in parallel, probably saving around 180 seconds — but we're
only medium-confident, so let's prove it.* Proving it is the shadow runner's job,
covered next.
