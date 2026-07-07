# 01 — CI, Jobs, Workflows, and Runners

This page assumes you know nothing about automated software checks. Every term is
defined the moment it appears.

## What is CI?

**CI** stands for **Continuous Integration**. "Integration" just means combining
everyone's code changes into the one shared copy of the project. "Continuous"
means it happens constantly, all day, every time anyone makes a change.

The problem CI solves: imagine ten people editing the same document. Person A's
edit might accidentally break something Person B wrote. If nobody notices for two
weeks, untangling it is miserable. CI is a robot that, the instant you submit a
change, re-checks the whole project to confirm it still works. If you broke
something, you find out in minutes, not weeks.

## What is a job?

A **job** is one single check the robot performs. A project usually has several.
Typical jobs:

- **lint** — check the code is formatted neatly and follows the house style.
- **test** — run the project's automated tests to confirm the behaviour is correct.
- **build** — assemble the finished, runnable version of the program.

Each job is independent in the sense that it's a separate task with its own
result: it either **passes** (everything fine) or **fails** (something's wrong).

## What is a workflow file?

A **workflow file** is a plain text file, written in a format called **YAML**,
that lists the jobs and the rules for running them. It lives inside the project
in a folder called `.github/workflows/`. GitHub reads this file and does exactly
what it says.

**YAML** is just a way of writing structured information using indentation
(spaces) to show what belongs to what. Things lined up under a heading belong to
that heading.

## What is a runner?

A **runner** is the actual computer that performs a job. When GitHub needs to run
the `test` job, it rents a fresh virtual computer, runs the tests on it, records
whether they passed, and then throws the computer away. That rented computer is
the runner. The key fact for this whole tool: **GitHub can rent many runners at
once**, so several jobs can run on separate computers at the same time.

## A real workflow file, line by line

```yaml
name: CI                          # a human-friendly name for this workflow
on: [push]                        # WHEN to run: on every "push" (code submission)
jobs:                             # the list of jobs begins here
  lint:                           # job #1, named "lint"
    runs-on: ubuntu-latest        # rent a Linux runner for this job
    steps:                        # the ordered actions inside the job
      - uses: actions/checkout@v4 # step 1: copy the project onto the runner
      - run: ruff check .         # step 2: run the linter on all the code
  test:                           # job #2, named "test"
    needs: lint                   # WAIT for "lint" to finish before starting
    runs-on: ubuntu-latest        # rent a (separate) Linux runner
    steps:
      - uses: actions/checkout@v4 # copy the project onto this runner too
      - run: pytest               # run the test suite
```

Reading it in plain English: "This workflow is called CI. Run it whenever someone
pushes code. It has two jobs. `lint` runs on a Linux machine and checks code
style. `test` also runs on a Linux machine, runs the tests — but it must wait for
`lint` to finish first, because of that `needs: lint` line."

That one line, `needs: lint`, is the heart of everything this tool does. The next
page explains why.
