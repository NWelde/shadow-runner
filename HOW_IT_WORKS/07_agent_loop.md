# 07 — The Agent Loop

## When a shadow run fails

Recall from page 04 the danger: a **hidden dependency**. The graph said two jobs
were independent, so the tool removed a `needs:` edge — but the job secretly
relied on something the other job set up. When that happens, the proposed run
doesn't just run slower; it **fails**. The job tries to do its work, finds the
thing it quietly needed is missing, and errors out.

This is not a disaster. It is exactly why we experiment on a throwaway fork
instead of on the real project. A failed experiment costs a few minutes of
compute and teaches us something true. A failed *pull request* costs a
maintainer's trust.

## How the failure log tells us about hidden dependencies

When a job fails, GitHub keeps its **log** — the full text output of everything
the job printed while running. The tool fetches this with `read_failure_signal`
(in `src/shadow/observe.py`), which downloads the raw log for the failed job.

The log usually names the missing thing outright. For example, if `test` was
unblocked from waiting on `build`, the log might say:

```
Error: cannot find ./dist/app.bundle — no such file or directory
```

That single line is the hidden dependency confessing itself: `test` needed the
`./dist/app.bundle` file, which `build` produces. So `needs: build` was *not*
unnecessary after all. The graph looked safe; reality disagreed; the log
explained why.

## What the retry loop looks like

The "agent loop" is the cycle of propose → experiment → read result → decide:

```
   ┌─────────────────────────────────────────────────────────┐
   │                                                           │
   ▼                                                           │
 pick the next candidate change (from triage's findings)       │
   │                                                           │
   ▼                                                           │
 run the shadow experiment (baseline vs proposed)              │
   │                                                           │
   ▼                                                           │
 read the verdict                                              │
   │                                                           │
   ├── speedup + passed ──▶ success! go write the pitch ───────┘ (done)
   │
   ├── failure ──▶ read the log, mark this change as tried,
   │               and loop back to pick a DIFFERENT candidate
   │
   └── no_change / regression ──▶ not worth it; mark as tried,
                                  loop back to a different candidate
```

The memory of what's already been tried lives in the database
(`load_past_experiments` in `src/shadow/memory.py`), so the loop never proposes
the same change twice — each pass through picks a fresh candidate from the triage
findings or the next parallelism opportunity in the graph.

## When we give up and move on

The loop is not infinite. It stops in any of these cases:

- **A win is found.** A proposed change is both faster and still passing. We stop
  immediately and go to the pitch — one solid, proven improvement is the goal.
- **The candidates run out.** Every parallelism opportunity and finding has been
  tried, and none produced a clean speedup. There's nothing left to test.
- **A safety budget is hit.** To avoid burning unlimited compute, the loop caps
  how many experiments it will run. Past that, it stops and reports what it
  learned, even if that's "no safe speedup found here."

"No safe speedup found" is itself a legitimate, honest result. The tool's promise
is measured truth, not a guaranteed win — and a measured "this pipeline is
already efficient" is worth knowing too.
