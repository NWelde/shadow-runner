"""Build and analyse the weighted job-dependency graph with ``networkx``.

A CI pipeline is a directed acyclic graph (DAG): each job is a node, and a
``needs:`` relationship is an edge pointing from the dependency to the dependent
(``lint -> test`` means "test waits for lint"). Weighting each node by the job's
average runtime lets us find the **critical path** — the chain whose total time
sets the floor on how fast the whole pipeline can possibly finish.

``build_dep_graph`` (parsing YAML into an adjacency map) is intentionally
re-exported from ``triage.repo_profile_input`` so there is a single
implementation of that parse shared by the triage and graph layers.
"""

from __future__ import annotations

import networkx as nx

from triage.repo_profile_input import build_dep_graph

__all__ = [
    "build_dep_graph",
    "build_weighted_graph",
    "critical_path",
    "parallelism_opportunities",
]


def build_weighted_graph(
    dep_graph: dict[str, list[str]], avg_durations: dict[str, float]
) -> nx.DiGraph:
    """Turn an adjacency map + per-job durations into a weighted ``DiGraph``.

    Every job becomes a node carrying ``weight`` = its average runtime in
    seconds (0.0 if unknown). Each ``needs`` relationship becomes an edge from
    the dependency to the dependent, so edge direction follows execution order.
    """
    graph = nx.DiGraph()
    for job in dep_graph:
        graph.add_node(job, weight=float(avg_durations.get(job, 0.0)))
    for job, deps in dep_graph.items():
        for dep in deps:
            if dep not in graph:
                graph.add_node(dep, weight=float(avg_durations.get(dep, 0.0)))
            graph.add_edge(dep, job)
    return graph


def critical_path(graph: nx.DiGraph) -> list[str]:
    """Return the longest path by total node weight (root -> leaf).

    Uses dynamic programming over a topological ordering: for each node, the
    heaviest path ending at it is its own weight plus the heaviest path ending
    at its weightiest predecessor. The summed duration of the returned path is
    the minimum possible wall-clock time for the pipeline, no matter how much
    parallelism is available. Raises ``ValueError`` if the graph has a cycle.
    """
    try:
        order = list(nx.topological_sort(graph))
    except nx.NetworkXUnfeasible as exc:
        raise ValueError("graph contains a cycle") from exc

    dist: dict[str, float] = {}
    parent: dict[str, str | None] = {}
    for node in order:
        weight = float(graph.nodes[node].get("weight", 0.0))
        best_pred, best_dist = None, 0.0
        for pred in graph.predecessors(node):
            if dist[pred] > best_dist:
                best_pred, best_dist = pred, dist[pred]
        dist[node] = best_dist + weight
        parent[node] = best_pred

    if not dist:
        return []

    end = max(dist, key=lambda n: dist[n])
    path: list[str] = []
    while end is not None:
        path.append(end)
        end = parent[end]
    path.reverse()
    return path


def parallelism_opportunities(
    graph: nx.DiGraph, avg_durations: dict[str, float]
) -> list[dict]:
    """Find job pairs that share a dependency but are independent of each other.

    Two jobs are a candidate for parallel execution when they have a common
    direct dependency yet neither (transitively) needs the other. The estimated
    saving is the duration of the *shorter* job: that work can overlap with the
    longer one instead of running back-to-back. Returns one dict per
    (pair, shared dependency): ``{job_a, job_b, shared_dep, estimated_saving_s}``.
    """
    opportunities: list[dict] = []
    nodes = list(graph.nodes)
    for i, job_a in enumerate(nodes):
        for job_b in nodes[i + 1 :]:
            shared = set(graph.predecessors(job_a)) & set(graph.predecessors(job_b))
            if not shared:
                continue
            if nx.has_path(graph, job_a, job_b) or nx.has_path(graph, job_b, job_a):
                continue
            saving = min(
                float(avg_durations.get(job_a, 0.0)),
                float(avg_durations.get(job_b, 0.0)),
            )
            for dep in sorted(shared):
                opportunities.append(
                    {
                        "job_a": job_a,
                        "job_b": job_b,
                        "shared_dep": dep,
                        "estimated_saving_s": saving,
                    }
                )
    return opportunities
