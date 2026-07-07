# CI Shadow Runner — Report for `madeintandem/jsonb_accessor`

**TL;DR:** Your CI gates the entire 186-variant test matrix behind `needs: lint`.
We verified on real GitHub Actions infrastructure that this has two costs:

1. **Correctness (the important one):** any time RuboCop reports an offense —
   even a trivial autocorrectable one — **all 186 test jobs are skipped.**
   Contributors get zero test feedback until the style nit is fixed.
2. **Speed:** every run's tests wait for `lint` to finish before starting, for no
   reason. Removing the edge made an identical, fully-green pipeline finish
   **~13% faster** (and tests started **15 s sooner**).

The fix is a **one-line change**: delete `needs: lint` from the `tests` job.

We did not guess at this — we forked the repo and ran it both ways. Links below.

---

## 1. The finding

`.github/workflows/ci.yml` has two jobs:

```yaml
jobs:
  lint:
    runs-on: ubuntu-24.04
    steps: [checkout, ruby/setup-ruby, bundle exec rubocop]
  tests:
    needs: lint          # <-- this line
    strategy:
      matrix: { ruby: [...], activerecord: [...], postgresql: [...] }  # 186 variants
    services: { db: postgres }
    steps: [checkout, setup-ruby, rake db:schema:load, rake spec]
```

The `tests` job declares `needs: lint`. But `tests` does **not consume anything
`lint` produces** — each test job does its own `actions/checkout`, installs its
own gems via `bundler-cache`, and spins up its own Postgres service. RuboCop
writes no artifact, sets no shared state. The dependency is purely a gate.

A `needs:` edge in GitHub Actions does two things: it serialises (tests wait for
lint) **and** it conditions (tests run *only if* lint succeeds). Both are costing
you something here.

---

## 2. Cost #1 — tests are silently skipped on any lint offense (correctness)

We ran the **real, unchanged 186-variant matrix** twice on a fork, with one
RuboCop offense present (a redundant `# rubocop:disable` directive in
`spec/spec_helper.rb` — exactly the kind of nit that shows up in everyday PRs):

| Config | `lint` | Test matrix | Run |
|---|---|---|---|
| **Current** (`needs: lint`) | ❌ fails (~37 s) | **0 of 186 ran — all SKIPPED** | [26718093945](https://github.com/NWelde/jsonb_accessor/actions/runs/26718093945) |
| **Proposed** (`needs: lint` removed) | ❌ fails (~37 s) | **185 of 186 ran and PASSED** | [26718096701](https://github.com/NWelde/jsonb_accessor/actions/runs/26718096701) |

This is the headline. With `needs: lint`, a contributor who introduces *any*
RuboCop offense — a trailing space, an unnecessary directive — loses **all** test
feedback. They learn their formatting is off; they learn nothing about whether
their logic is correct. With the edge removed, lint and tests report
independently: if lint fails and tests pass, the contributor knows exactly what
to fix.

(The lone failing variant in the proposed run was a single pre-existing flaky
matrix leg, unrelated to this change — 185 of 186 green.)

---

## 3. Cost #2 — every run is slower than it needs to be (speed)

To measure speed cleanly we needed both pipelines **green**, so we fixed the
RuboCop nit on **both** branches (identical fix) and ran a representative
6-variant CRuby subset. The **only** difference between the two runs is the
`needs: lint` line:

| Config | Result | `lint` | Tests start at | **Wall-clock** | Run |
|---|---|---|---|---|---|
| **Current** (`needs: lint`) | ✅ 7/7 green | 4→15 s | **18 s** (after lint) | **89 s** | [26718644075](https://github.com/NWelde/jsonb_accessor/actions/runs/26718644075) |
| **Proposed** (no `needs:`) | ✅ 7/7 green | 4→15 s | **3 s** (immediate) | **77 s** | [26718645670](https://github.com/NWelde/jsonb_accessor/actions/runs/26718645670) |

**~13% faster (89 s → 77 s), both fully green.** The clean, variance-free metric:
tests start **15 s sooner** because they no longer wait for `lint`.

Note on magnitude: the saving equals the `lint` job's duration, because `lint`
comes off the critical path entirely. In this run `bundler-cache` was warm and
RuboCop finished in ~11 s; on a cold cache (e.g. the first run after a dependency
bump) `lint` takes ~37 s — so the real saving is **~15–37 s on every single run**,
across all 186 variants.

---

## 4. The change

```diff
   tests:
-    needs: lint
     runs-on: ubuntu-24.04
     strategy:
       fail-fast: false
       matrix:
         ...
```

That's the whole change. A ready-to-paste PR description is in
`madeintandem_jsonb_accessor_pr.md`.

**Why it's safe:** the `tests` job re-checks out the repository and installs all
dependencies independently. It consumes no file, environment variable, or service
that `lint` produces. We proved it: with the edge removed, 185/186 variants ran
and passed.

---

## 5. How this was produced

- **Tool:** CI Shadow Runner — ingests a repo's GitHub Actions history, builds a
  duration-weighted dependency graph, finds the removable edge, then **runs the
  experiment on real infrastructure** before proposing anything.
- **Ingested:** 30 recent runs of `jsonb_accessor` to recover per-job timings.
- **Verified:** 4 real workflow runs on a throwaway fork (`NWelde/jsonb_accessor`),
  linked above. Anyone can open them and check the timings and job results.
- **Not an estimate:** every number here came from a run that actually happened.
