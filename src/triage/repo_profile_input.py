# Read this from github

# This is supposed to inpu the current job dependency from the yaml, the needs: part of the yaml
# Average duration of a job
# Failure rate of a job
# Longest chain of dependencies(I dont know what to do with this though)

# For the agent loop
# We want to know that when this is the second time we are trying to run this agent. The triage part has to take in the experiment and other inputs from the other parts of the agent loop.
import statistics as stats
from collections import defaultdict
from types import SimpleNamespace

import yaml

from store import connect, read_repo_profile


def load_profile(db_path: str, repo_id: int):
    """Load a RepoProfile from the SQLite DB. Caller owns the repo_id."""
    conn = connect(db_path)
    try:
        return read_repo_profile(conn, repo_id)
    finally:
        conn.close()


# NOTE: job dependencies come from ``build_dep_graph`` (parses the YAML into a
# named adjacency map). The old ``read_job_deps`` returned a nameless list of
# dep-lists and has been removed.


def read_av_job_duration(profile_dict) -> dict[str, float]:
    """Mean job duration in seconds, broken down per job name.

    Groups every job by name across all runs and averages the durations, e.g.
    ``{"lint": 42.3, "test": 487.1}``. The per-job breakdown is what lets the
    critical path be weighted by time (see ``chain_of_deps``).
    """
    durations: dict[str, list[float]] = defaultdict(list)
    for run in profile_dict.runs:
        for job in run.jobs:
            durations[job.name].append(job.duration_s)
    return {name: stats.mean(vals) for name, vals in durations.items()}


def read_failure_rate(profile_dict, job_name=None) -> float:
    """Failure rate as a percentage, keyed on the run job *name*.

    ``job_name`` selects which run jobs to count: ``None`` counts every job, a
    single string matches that exact name, and a collection (set/list) matches
    any name in it -- the last form lets a matrixed YAML job pool all of its
    legs (see ``names_by_job_id``). A job is "failed" when its GitHub
    *conclusion* is ``"failure"`` -- not its lifecycle ``status`` (which is
    almost always ``"completed"`` and would make this return 0 forever).
    """
    total_jobs = 0
    failed_jobs = 0

    for run in profile_dict.runs:
        for job in run.jobs:
            if job_name is None:
                matches = True
            elif isinstance(job_name, str):
                matches = job.name == job_name
            else:
                matches = job.name in job_name
            if matches:
                total_jobs += 1
                if job.conclusion == "failure":
                    failed_jobs += 1

    if total_jobs == 0:
        return 0.0
    return (failed_jobs / total_jobs) * 100


def build_dep_graph(raw_yaml: str) -> dict[str, list[str]]:
    """Parse a workflow's YAML into a job-dependency adjacency map.

    Returns ``{job_name: [jobs it directly needs]}`` with every job present as
    a key (jobs with no ``needs:`` map to an empty list, so they are roots).
    ``needs`` may be absent, a single string, or a list of strings in the YAML;
    all three are normalised to a list here.
    """
    config = yaml.safe_load(raw_yaml) or {}
    jobs = config.get("jobs", {})

    graph: dict[str, list[str]] = {}
    for job_name, job_config in jobs.items():
        needs = (job_config or {}).get("needs")
        if needs is None:
            graph[job_name] = []
        elif isinstance(needs, str):
            graph[job_name] = [needs]
        else:
            graph[job_name] = list(needs)
    return graph


def chain_of_deps(deps: dict[str, list[str]], durations: dict[str, float]) -> list[str]:
    """Return the critical path: the dependency chain with the greatest total time.

    ``deps`` maps each job to the jobs it directly needs (see
    ``build_dep_graph``); ``durations`` maps each job to its average runtime in
    seconds (see ``read_av_job_duration``). The path is weighted by duration,
    not by hop count -- a single slow job (test, 480s) outweighs many fast hops
    (lint -> build -> deploy, 1s each). The result is ordered root -> leaf and
    its summed duration is the floor on CI wall-clock time regardless of
    available parallelism. A job missing from ``durations`` contributes 0s.

    Raises ``ValueError`` if the graph contains a cycle.
    """

    def total(chain: list[str]) -> float:
        return sum(durations.get(j, 0.0) for j in chain)

    def key(chain: list[str]) -> tuple[float, int]:
        # Rank by total duration, then by hop count so ties never drop an edge
        # (any real chain outranks the empty chain on length at equal weight).
        return (total(chain), len(chain))

    # Heaviest chain *ending at* each job, memoised. A job's heaviest chain is
    # itself appended to the heaviest chain among its dependencies.
    best: dict[str, list[str]] = {}
    on_stack: set[str] = set()  # nodes in the current DFS path -> cycle guard

    def heaviest_to(job: str) -> list[str]:
        if job in best:
            return best[job]
        if job in on_stack:
            raise ValueError(f"cycle detected in job dependencies at {job!r}")
        on_stack.add(job)

        heaviest_prefix: list[str] = []
        for dep in deps.get(job, []):
            prefix = heaviest_to(dep)
            if key(prefix) > key(heaviest_prefix):
                heaviest_prefix = prefix

        on_stack.discard(job)
        best[job] = heaviest_prefix + [job]
        return best[job]

    overall: list[str] = []
    for job in deps:
        chain = heaviest_to(job)
        if key(chain) > key(overall):
            overall = chain
    return overall


def _workflow_job_names(raw_yaml: str) -> dict[str, str | None]:
    """Map each YAML job id to its static ``name:`` override (or ``None``).

    The runs API reports a job's *display name*, which is the YAML ``name:`` when
    set. Templated names (containing ``${{ ... }}``) can't be resolved without
    evaluating matrix expressions, so they're treated as no override (``None``)
    and matching falls back to the job id.
    """
    config = yaml.safe_load(raw_yaml) or {}
    jobs = config.get("jobs", {})
    overrides: dict[str, str | None] = {}
    for job_id, job_config in jobs.items():
        name = (job_config or {}).get("name")
        overrides[job_id] = (
            name if isinstance(name, str) and "${{" not in name else None
        )
    return overrides


def match_job_id(
    run_job_name: str,
    dep_graph: dict[str, list[str]],
    name_overrides: dict[str, str | None],
) -> str | None:
    """Resolve a run's display job name back to the YAML job id it belongs to.

    The dependency graph keys off YAML job *ids* while durations/failure-rates
    key off run job *names*; this is the join between them. GitHub names a job
    after its id by default, expands matrix legs to ``"<id> (<values>)"``, and
    uses the YAML ``name:`` when present. We match, in order: id exactly, id as a
    matrix prefix, a static ``name:`` exactly, that name as a matrix prefix.
    Returns ``None`` when nothing matches (e.g. a templated name).
    """
    for job_id in dep_graph:
        if run_job_name == job_id or run_job_name.startswith(f"{job_id} ("):
            return job_id
    for job_id, custom in name_overrides.items():
        if custom and (
            run_job_name == custom or run_job_name.startswith(f"{custom} (")
        ):
            return job_id
    return None


def names_by_job_id(
    profile_dict,
    dep_graph: dict[str, list[str]],
    name_overrides: dict[str, str | None],
) -> dict[str, set[str]]:
    """Group every observed run job name under the YAML job id it maps to."""
    grouped: dict[str, set[str]] = defaultdict(set)
    for run in profile_dict.runs:
        for job in run.jobs:
            job_id = match_job_id(job.name, dep_graph, name_overrides)
            if job_id is not None:
                grouped[job_id].add(job.name)
    return grouped


def _durations_by_job_id(
    per_name_durations: dict[str, float],
    dep_graph: dict[str, list[str]],
    name_overrides: dict[str, str | None],
) -> dict[str, float]:
    """Collapse per-name mean durations onto YAML job ids for critical-path use.

    A matrix job's legs run in parallel and a downstream ``needs:`` waits for the
    slowest, so the node's wall-clock contribution is the **max** across its legs,
    not their sum or mean.
    """
    by_id: dict[str, float] = {}
    for name, mean in per_name_durations.items():
        job_id = match_job_id(name, dep_graph, name_overrides)
        if job_id is not None:
            by_id[job_id] = max(by_id.get(job_id, 0.0), mean)
    return by_id


def _analyse_workflow(profile_dict, workflow) -> dict:
    """Build the triage signals for a single workflow, keyed by YAML job id.

    Durations and failure rates are scoped to *this workflow's* runs (matched by
    ``Run.workflow_id``). Without that scoping, two workflows sharing a job id
    (e.g. both define ``build``) would pool each other's timings.
    """
    dep_graph = build_dep_graph(workflow.raw_yaml)
    overrides = _workflow_job_names(workflow.raw_yaml)

    scoped = SimpleNamespace(
        runs=[r for r in profile_dict.runs if r.workflow_id == workflow.id]
    )

    per_name_durations = read_av_job_duration(scoped)
    durations = _durations_by_job_id(per_name_durations, dep_graph, overrides)

    grouped_names = names_by_job_id(scoped, dep_graph, overrides)
    failure_rates = {
        job_id: read_failure_rate(scoped, grouped_names.get(job_id, set()))
        for job_id in dep_graph
    }

    critical_path = chain_of_deps(dep_graph, durations)
    return {
        "critical_path_workflow": workflow.filename,
        "dep_graph": dep_graph,
        "avg_durations": durations,
        "failure_rates": failure_rates,
        "critical_path": critical_path,
        "critical_path_duration_s": sum(durations.get(j, 0.0) for j in critical_path),
    }


def build_triage_input(profile_dict) -> dict:
    """Assemble the single structured object handed to the triage agent.

    Each workflow is an independent DAG (its own run), so this analyses *every*
    workflow and returns the one whose duration-weighted critical path is the
    longest -- that path is the repo's CI wall-clock floor. The returned signals
    (``dep_graph``, ``avg_durations``, ``failure_rates``, ``critical_path``) are
    all keyed by that workflow's YAML job ids, reconciled against the run job
    names via ``match_job_id``. ``profile_dict`` is a ``RepoProfile`` (e.g. from
    ``load_profile``).
    """
    if not profile_dict.workflows:
        raise ValueError("profile has no workflows to analyse")

    analyses = [_analyse_workflow(profile_dict, wf) for wf in profile_dict.workflows]
    return max(analyses, key=lambda a: a["critical_path_duration_s"])
