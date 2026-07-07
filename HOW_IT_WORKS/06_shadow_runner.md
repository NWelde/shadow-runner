# 06 — The Shadow Runner

## Why experiment before opening a pull request

A **pull request** (PR) is the formal way you propose a change to a project: you
say "here is my edit, please review and merge it." Maintainers are busy and
skeptical, and rightly so. A PR that says "this *should* make CI faster" invites
an argument. A PR that says "I ran it twice on real infrastructure; here are the
two run links; it *was* 180 seconds faster and all checks still passed" is hard
to argue with.

So before touching the real project at all, the tool runs a **shadow experiment**:
a private dress rehearsal of the change, measured, with the real project none the
wiser. "Shadow" because it mirrors the real pipeline off to the side.

## What forking a repo means

A **fork** is your own complete copy of someone else's project, under your own
account. You can do anything to a fork — push branches, run CI, break things,
delete it — and the original project is completely unaffected. The tool forks the
target repo specifically so it has a sandbox to run experiments in.

When the experiment is done, the fork is deleted. It was scaffolding, not a
keepsake.

## What workflow dispatch is

Normally CI runs when someone pushes code. But we want to trigger runs on demand,
twice, on command. GitHub provides exactly this: **workflow dispatch**, a way to
say "run this workflow now" through the API. A workflow has to opt in by listing
`workflow_dispatch` as one of its triggers, so the tool makes sure the workflow
files it pushes to the fork include that trigger.

The dispatch is sent with a plain HTTP request:

```
POST /repos/{owner}/{repo}/actions/workflows/{workflow_id}/dispatches
body: { "ref": "the-branch-to-run" }
```

## How the experiment is set up

Inside the fork, the tool creates two versions of the pipeline:

1. **Baseline** — the original workflow, unchanged, on the fork's default branch.
2. **Proposed** — the workflow with one `needs:` edge removed (the
   `propose_yaml_change` output), on a second branch called `shadow-proposed`.

Then it dispatches one run of each. Both run on GitHub's real runners, on real
machines, doing the real work — the only difference between them is the single
line that was removed.

## How we measure the time difference

The tool watches both runs until they finish (`poll_until_complete` in
`src/shadow/observe.py`, checking every 30 seconds). For each run it reads the
real start and end timestamps GitHub records, and computes the wall-clock
duration. Then `compare_runs` produces the verdict:

```python
{
  "baseline_duration_s": 660.0,
  "proposed_duration_s": 480.0,
  "saving_s": 180.0,
  "saving_pct": 27.3,
  "proposed_passed": true,        # did the changed version still pass all checks?
  "verdict": "speedup"            # speedup | no_change | regression | failure
}
```

`proposed_passed` is critical: a change that's faster but **breaks the build** is
not a win, it's a `failure`. Speed only counts if correctness is preserved.

## What a shadow run looks like in the GitHub Actions UI

If you opened the fork's "Actions" tab during an experiment, you'd see two runs of
the same workflow listed, one triggered on the default branch and one on
`shadow-proposed`, each with the familiar pipeline view: the jobs as boxes,
arrows showing what waited for what, and a stopwatch on each. In the baseline
you'd see `test` starting only after `build` finished (a staircase). In the
proposed run you'd see them starting together (side by side) — and the whole run
finishing noticeably sooner. That visible difference is the evidence the pitch is
built from.

The next page covers what happens when the proposed run *doesn't* pass — when a
hidden dependency bites.
