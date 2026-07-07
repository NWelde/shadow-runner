# 04 — Parallelism

## What "running in parallel" physically means

**In parallel** means "at the same time, on different machines." Remember from
page 01 that GitHub rents a fresh computer (a runner) for each job, and it can
rent many at once. So if two jobs don't have to wait for each other, GitHub will
literally run them on two separate computers simultaneously.

The opposite is **serial** (or "sequential"): one after another on the timeline,
even if on different machines.

```
SERIAL (test waits for build, 180s + 480s):
  build  ████████                      (0s ──▶ 180s)
  test            ████████████████████ (180s ──▶ 660s)
  total wall-clock time: 660s

PARALLEL (test and build run at once):
  build  ████████                      (0s ──▶ 180s)
  test   ████████████████████          (0s ──▶ 480s)
  total wall-clock time: 480s  ← the longer of the two
```

Same work, same machines, **180 seconds saved**, just by not making one wait for
the other.

## What makes parallel safe vs unsafe

Running two jobs in parallel is **safe** when neither one uses anything the other
one produces. `lint` (checking code style) and `test` (running tests) both just
read the project's code; neither needs the other's output. They are independent,
so running them together changes nothing about their results.

It is **unsafe** when one job genuinely depends on the other's output. If `test`
needs the compiled program that `build` produces, then starting `test` before
`build` finishes means `test` has nothing to run — it'll fail. That dependency is
**real** and the `needs:` line is correct.

## What a hidden dependency is

A **hidden dependency** is when job B *appears* independent of job A but secretly
relies on something A did — a file A wrote to a shared location, a service A
started, an environment A set up. On paper you could remove `needs: A` from B,
but in reality B would break because that invisible hand-off is gone.

Hidden dependencies are the whole reason this tool runs an **experiment** instead
of just editing the file. We can guess from the graph that a `needs:` looks
unnecessary, but we cannot be *sure* there's no hidden dependency until we
actually run the change and watch whether it still passes. (See page 07 for what
happens when a hidden dependency reveals itself by making the experiment fail.)

## Why removing a `needs:` edge is all GitHub needs

GitHub Actions runs jobs in parallel **by default**. It only makes a job wait
when you explicitly tell it to with a `needs:` line. So you don't have to write
any special "run these together" instruction — parallelism is the default, and
`needs:` is the brake.

That means the entire change this tool proposes is: **delete one `needs:` entry.**

```yaml
# before — test is forced to wait for build
test:
  needs: build
  runs-on: ubuntu-latest

# after — the brake is removed; GitHub now runs test and build at the same time
test:
  runs-on: ubuntu-latest
```

Removing that single line is what `propose_yaml_change()` in
`src/shadow/runner.py` does — carefully, leaving every other character of the
file untouched so the change is a clean one-line diff. The next page explains how
the tool decides *which* `needs:` line to target.
