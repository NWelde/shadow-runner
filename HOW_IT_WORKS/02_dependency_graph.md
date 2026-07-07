# 02 — The Dependency Graph

## What is a graph?

In everyday speech "graph" means a chart with bars or lines. In computing it
means something different: a **graph** is a set of dots connected by lines. The
dots are called **nodes** and the lines are called **edges**.

- A **node** is a thing. For us, each node is a job (lint, test, build).
- An **edge** is a relationship between two things. For us, each edge means "this
  job waits for that job."

That's the entire idea. Jobs are dots; waiting-relationships are lines between
the dots.

## What does "directed" mean?

An edge can be a plain line (no direction) or an arrow (a direction). When the
edges are arrows, we call it a **directed** graph. Direction matters for us
because "lint must finish before test" is not the same as "test must finish
before lint." The arrow points from the job that goes first to the job that waits:

```
   lint  ───▶  test
  (first)     (waits)
```

## What does "acyclic" mean, and why must CI be acyclic?

A **cycle** is a loop: you follow the arrows and end up back where you started.
**Acyclic** means "no cycles — no loops."

Why CI must be acyclic: suppose `test` waits for `build`, and `build` waits for
`test`. Then `test` can never start (it's waiting for `build`), and `build` can
never start (it's waiting for `test`). They wait for each other forever and
nothing ever runs. A loop of waiting is a deadlock. So a valid CI pipeline is
always a **DAG** — a **Directed Acyclic Graph**: arrows with no loops.

## How `needs:` becomes an edge

Every `needs:` line in the workflow file becomes exactly one arrow.

```yaml
jobs:
  lint:
    # no needs -> nothing points into lint; it's a starting point
  test:
    needs: lint        # edge:  lint ──▶ test
  build:
    needs: [lint, test]  # two edges:  lint ──▶ build   AND   test ──▶ build
```

A job with no `needs:` has no incoming arrows — it can start immediately. A job
can have several `needs:`, which means several arrows point into it, and it must
wait for *all* of them.

## A small example graph

The workflow above turns into this picture:

```
        ┌────────┐
        │  lint  │
        └───┬────┘
            │            (lint must finish first)
       ┌────┴─────┐
       ▼          ▼
   ┌────────┐  ┌────────┐
   │  test  │  │ build  │
   └───┬────┘  └────────┘
       │            ▲
       └────────────┘   (build also waits for test)
```

Read it as: lint goes first. Once lint is done, test can start. build has to wait
for both lint *and* test.

The tool builds exactly this graph in code (see `src/dag/graph.py`,
`build_dep_graph` and `build_weighted_graph`). Then it adds one more piece of
information to each node — how long that job takes on average — which is what the
next page uses to find the slowest path through the graph.
