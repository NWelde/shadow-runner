# 08 — The Pitch

## What the pitch artifact is

The **pitch** is the tool's final output: two short written documents produced
from the measured experiment.

1. A **report** — a one-page, plain-language summary a human can read in a minute:
   how slow the pipeline was, what we changed, how much faster it got, and whether
   it still passed.
2. A **PR body** — a ready-to-paste pull-request description, written in Markdown,
   with the evidence laid out so a maintainer can review the change and merge it
   with confidence.

Both are written by the local `gemma4:4b` model (see `write_pitch` in
`src/pitch/pitch.py`) and saved into the `outputs/` folder. The model is given the
real facts — repo name, job names, before/after durations, seconds and percent
saved, whether it passed, and the hypothesis tested — and told to write about
*this* repository specifically, never a generic template. If the model's output
can't be used, the tool falls back to a deterministic write-up built straight
from the numbers, so a pitch is always produced.

## Who it's for

The report is for **you** — the person running the tool — to understand and
sanity-check the result before sharing it.

The PR body is for the **maintainers** of the target project — the people who
decide whether to accept the change. They are the audience that matters, and they
are skeptical by default. The pitch is written to earn their trust fast.

## What it contains

A good pitch always includes:

- The **headline number**: "11m 0s → 8m 0s, 180s (27%) faster."
- The **hypothesis** that was tested, in one sentence.
- The **measured evidence**: both run durations, the saving, and — crucially —
  the confirmation that the proposed version **still passed all checks**.
- The fact that these are **real runs on real infrastructure**, not estimates.

## Why measured evidence beats a demo

A **demo** shows that something *can* work, once, under conditions you chose. It
proves possibility. **Measured evidence** shows that something *did* work, on the
real pipeline, with the numbers to back it — and that it didn't break anything.
It proves the actual claim.

For a CI change the difference is everything. "I think removing this `needs:`
saves time" is an opinion. "I ran the unchanged pipeline and the changed pipeline
back-to-back on GitHub's runners; here are both run links; the changed one
finished 180 seconds sooner and every check still passed" is a fact a reviewer
can verify in two clicks. The first invites debate; the second invites a merge.

## Example pitch report (with placeholder numbers)

```
CI Speedup Report — owner/example-repo
=======================================

Headline: CI went from 660s to 480s — 180s faster (27.3%).

Hypothesis tested:
  "build" was waiting on "test", but build only needs the source code,
  not the test results, so the wait is unnecessary.

What we did:
  Removed `needs: test` from the `build` job so build and test run in
  parallel instead of one after the other.

Measured evidence (two real GitHub Actions runs):
  - Baseline (original config):  660s
  - Proposed (one line removed): 480s
  - Saving:                      180s (27.3%)
  - Proposed run result:         PASSED all checks

Verdict: speedup. Safe to propose.
```

The matching PR body says the same thing in Markdown, with a summary line, the
evidence as a bullet list or table, and a note that the numbers come from a
shadow experiment rather than a guess — ready to paste straight into a new pull
request.
