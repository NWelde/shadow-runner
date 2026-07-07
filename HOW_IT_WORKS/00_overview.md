# 00 — Overview

## What this tool does, in one paragraph

Every time a programmer saves their work to a shared project, a set of automated
checks runs to make sure nothing is broken — checking the code style, running the
tests, building the program. That bundle of checks is called **CI** (Continuous
Integration), and each individual check is called a **job**. On big projects this
can take ten or fifteen minutes, and developers sit and wait for it every single
time. A lot of that waiting is unnecessary: some jobs are told to wait for other
jobs to finish even though they don't actually use anything those other jobs
produce. **CI Shadow Runner** reads a project's CI setup, works out which jobs
are slow and which waiting is unnecessary, proposes removing one unnecessary
wait, and then — before suggesting any change to the real project — quietly runs
both the old version and the new version on real infrastructure to measure the
actual time difference. It finishes by writing a short report: "your CI takes 11
minutes; we ran an experiment; with this one change it takes 6 minutes; here are
the logs."

The important word is **measured**. The tool never says "this should be faster."
It says "we ran it, and it *was* faster, by this many seconds."

## The four steps

```
  ┌──────────────┐     ┌──────────────┐     ┌──────────────┐     ┌──────────────┐
  │  1. INGEST   │     │  2. TRIAGE   │     │  3. SHADOW   │     │  4. PITCH    │
  │              │     │              │     │              │     │              │
  │ Read the CI  │ ──▶ │ Find slow &  │ ──▶ │ Run old vs   │ ──▶ │ Write the    │
  │ config + run │     │ unnecessary  │     │ new for real │     │ report + PR  │
  │ history from │     │ waits; build │     │ & measure    │     │ with the     │
  │ GitHub       │     │ the graph    │     │ the speedup  │     │ real numbers │
  └──────────────┘     └──────────────┘     └──────────────┘     └──────────────┘
        │                     │                     │                     │
        ▼                     ▼                     ▼                     ▼
   SQLite database      critical path +       two real GitHub       outputs/*.md
   of jobs & timings    candidate change      Actions runs          (report + PR body)
```

Each step has its own short guide in this folder:

- `01_ci_and_jobs.md` — what CI, jobs, workflows, and runners actually are
- `02_dependency_graph.md` — how "job B waits for job A" becomes a graph
- `03_critical_path.md` — the chain that decides the minimum possible time
- `04_parallelism.md` — why running jobs side by side is safe (and when it isn't)
- `05_triage.md` — how the tool decides what to flag
- `06_shadow_runner.md` — how we test a change without touching the real project
- `07_agent_loop.md` — what happens when an experiment fails
- `08_pitch.md` — the final report and why measured evidence beats a demo
