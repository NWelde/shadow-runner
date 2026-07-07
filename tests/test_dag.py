"""Tests for the weighted dependency graph: parsing, critical path, parallelism."""

import pytest

from dag.graph import (
    build_dep_graph,
    build_weighted_graph,
    critical_path,
    parallelism_opportunities,
)

YAML_MIXED = """
name: CI
on: push
jobs:
  lint:
    runs-on: ubuntu-latest
  test:
    needs: lint
    runs-on: ubuntu-latest
  build:
    needs: [lint, test]
    runs-on: ubuntu-latest
"""


def test_build_dep_graph_handles_none_string_list():
    graph = build_dep_graph(YAML_MIXED)
    assert graph["lint"] == []  # no needs -> empty list (root)
    assert graph["test"] == ["lint"]  # string needs -> single-item list
    assert graph["build"] == ["lint", "test"]  # list needs preserved


def test_build_dep_graph_every_job_is_a_key():
    graph = build_dep_graph(YAML_MIXED)
    assert set(graph) == {"lint", "test", "build"}


def _three_job_graph():
    dep_graph = {"lint": [], "test": ["lint"], "build": ["lint"]}
    durations = {"lint": 40.0, "test": 480.0, "build": 180.0}
    return build_weighted_graph(dep_graph, durations), durations


def test_critical_path_picks_heaviest_chain():
    graph, _ = _three_job_graph()
    # lint->test totals 520s, lint->build only 220s, so test wins.
    assert critical_path(graph) == ["lint", "test"]


def test_critical_path_raises_on_cycle():
    graph = build_weighted_graph({"a": ["b"], "b": ["a"]}, {"a": 1.0, "b": 1.0})
    with pytest.raises(ValueError, match="cycle"):
        critical_path(graph)


def test_parallelism_opportunities_finds_independent_pair():
    graph, durations = _three_job_graph()
    opps = parallelism_opportunities(graph, durations)
    assert len(opps) == 1
    opp = opps[0]
    assert {opp["job_a"], opp["job_b"]} == {"test", "build"}
    assert opp["shared_dep"] == "lint"
    assert opp["estimated_saving_s"] == 180.0  # the shorter of test/build


def test_parallelism_opportunities_none_when_serial_chain():
    # Pure chain lint->test->build: no two jobs share a dependency.
    graph = build_weighted_graph(
        {"lint": [], "test": ["lint"], "build": ["test"]},
        {"lint": 1.0, "test": 1.0, "build": 1.0},
    )
    assert parallelism_opportunities(graph, {"lint": 1.0}) == []
