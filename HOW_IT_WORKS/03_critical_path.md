# 03 — The Critical Path

## What is the critical path?

The **critical path** is the single chain of jobs, following the waiting-arrows,
that takes the **longest total time**. It matters because it sets the floor on how
fast the whole pipeline can possibly finish. No matter how many computers you
rent, the pipeline cannot finish sooner than its longest chain of must-happen-in-
order jobs.

A useful mental image: a relay race where some runners must hand off a baton in
sequence while others run their own independent laps. The race isn't over until
the *longest* sequence of hand-offs is done. That longest sequence is the
critical path.

## Why "longest by time," not "most jobs"

A chain of five tiny jobs (1 second each = 5 seconds total) is shorter in time
than a chain of two jobs where one of them takes 8 minutes. So we measure the
critical path by **total seconds**, weighting each node by how long that job
actually takes. A single slow job outweighs many fast ones.

## A worked example with three jobs

Suppose we measured these average durations from the project's run history:

| Job   | Average duration |
|-------|------------------|
| lint  | 40 seconds       |
| test  | 480 seconds      |
| build | 180 seconds      |

And the waiting-rules are:

- `test` needs `lint`
- `build` needs `lint`

So the graph is:

```
            ┌────────┐
            │  lint  │  40s
            └───┬────┘
          ┌─────┴─────┐
          ▼           ▼
     ┌────────┐   ┌────────┐
     │  test  │   │ build  │
     │  480s  │   │  180s  │
     └────────┘   └────────┘
```

### Step by step

There are two chains you can walk from a starting job to an ending job:

1. **lint → test**: 40 + 480 = **520 seconds**
2. **lint → build**: 40 + 180 = **220 seconds**

The longest by time is chain 1, **lint → test, at 520 seconds**. That is the
critical path. It means: even though `build` exists, the pipeline's minimum
possible time is 520 seconds, because `lint → test` is the slowest unavoidable
sequence. (`build` finishes at 40 + 180 = 220s, comfortably before test's 520s,
so it never holds anything up.)

This is exactly what `critical_path()` in `src/dag/graph.py` computes: it walks
the jobs in dependency order and, for each job, records the heaviest chain that
ends at it (its own time plus the heaviest chain of whatever it waits for).

## Why making a job faster only helps if it's on the critical path

Here's the counter-intuitive part. Suppose you spend a week making `build` twice
as fast: from 180s down to 90s. How much faster does the pipeline get?

**Zero.** Not one second. `build` was never on the critical path — the pipeline
was always waiting on the 520-second `lint → test` chain, and it still is. You
optimised a job that was already finishing early.

Now suppose instead you make `test` faster: from 480s down to 240s. The critical
path becomes lint → test = 40 + 240 = **280 seconds**, *unless* lint → build
(220s) is now longer... it isn't, so the pipeline drops from 520s to 280s. That's
a real, measurable win, because `test` *was* on the critical path.

**The rule:** only changes to jobs on the critical path can shorten the pipeline.
This is why the tool finds the critical path first — it tells us where effort is
worth spending, and where it's wasted.

The next page covers the other lever: instead of making a critical-path job
faster, we move it *off* the critical path by letting it run at the same time as
something else.
