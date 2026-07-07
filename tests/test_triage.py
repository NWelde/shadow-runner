"""Tests for the triage layer.

The model-calling path needs a live Ollama instance, so it is skipped; the
Finding schema validation is testable without one.
"""

import pytest

from triage.triage import Finding


def test_finding_schema_accepts_valid_payload():
    finding = Finding(
        job="build",
        dependency_to_remove="test",
        problem="build waits on test but needs no test artifact",
        type="parallelism_opportunity",
        estimated_saving_s=180.0,
        confidence="high",
    )
    assert finding.dependency_to_remove == "test"
    assert finding.type == "parallelism_opportunity"


def test_finding_dependency_to_remove_is_optional():
    finding = Finding(
        job="test",
        problem="slowest job on the critical path",
        type="slow_on_critical_path",
        estimated_saving_s=0.0,
        confidence="medium",
    )
    assert finding.dependency_to_remove is None


@pytest.mark.skip(reason="requires ollama")
def test_run_triage_live():
    from triage.triage import run_triage

    findings = run_triage(
        {
            "dep_graph": {"lint": [], "test": ["lint"], "build": ["lint"]},
            "avg_durations": {"lint": 42.3, "test": 487.1, "build": 183.4},
            "failure_rates": {"lint": 2.1, "test": 8.4, "build": 0.0},
            "critical_path": ["lint", "test"],
            "critical_path_duration_s": 529.4,
        }
    )
    assert all(isinstance(f, Finding) for f in findings)
