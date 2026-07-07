## Run tests in parallel with lint (remove `needs: lint`)

### Summary

The `tests` job in `.github/workflows/ci.yml` declares `needs: lint`. This forces
the entire 186-variant test matrix to **wait for RuboCop**, and — more
importantly — to be **skipped entirely whenever RuboCop reports any offense**.

Since the test jobs don't consume anything `lint` produces (each does its own
checkout, gem install, and Postgres service), this PR removes the dependency so
lint and tests report independently.

### Why

**Test feedback shouldn't depend on style.** With `needs: lint`, a single
autocorrectable RuboCop nit causes GitHub to skip all 186 test jobs — a
contributor gets no signal about whether their code actually works until they fix
formatting. I verified this on a fork, with one redundant `# rubocop:disable`
directive present (the kind of thing that lands in PRs all the time):

- **With `needs: lint`** → lint fails, **0 / 186 test jobs run** ([run](https://github.com/NWelde/jsonb_accessor/actions/runs/26718093945))
- **Without it** → lint still fails, but **185 / 186 test jobs run and pass** ([run](https://github.com/NWelde/jsonb_accessor/actions/runs/26718096701))

**It's also faster.** On an identical, fully-green pipeline (representative
6-variant subset, lint nit fixed on both sides so the only difference is this one
line):

| | Result | Tests start | Wall-clock |
|---|---|---|---|
| `needs: lint` | ✅ 7/7 | 18 s (after lint) | 89 s ([run](https://github.com/NWelde/jsonb_accessor/actions/runs/26718644075)) |
| without | ✅ 7/7 | 3 s (immediate) | **77 s** ([run](https://github.com/NWelde/jsonb_accessor/actions/runs/26718645670)) |

~13% faster here; the saving equals the `lint` duration removed from the critical
path (~15 s warm cache, ~37 s cold), on every run.

### The change

```diff
   tests:
-    needs: lint
     runs-on: ubuntu-24.04
     strategy:
       fail-fast: false
       matrix:
```

### Safety

The `tests` job re-checks out the repo and installs all dependencies
independently — it uses no artifact, env var, or service produced by `lint`.
Verified: with the edge removed, 185/186 matrix variants ran and passed. `lint`
still runs on every push/PR and still reports pass/fail; it just no longer blocks
or hides the test suite.
