"""Tests for compare_runs — saving calculation and verdict assignment."""

from shadow.observe import compare_runs


def _run(duration_s, conclusion="success"):
    return {"duration_s": duration_s, "conclusion": conclusion}


def test_speedup_verdict_and_saving():
    result = compare_runs(_run(600.0), _run(360.0))
    assert result["saving_s"] == 240.0
    assert result["saving_pct"] == 40.0
    assert result["proposed_passed"] is True
    assert result["verdict"] == "speedup"


def test_no_change_within_threshold():
    result = compare_runs(_run(600.0), _run(590.0))
    assert result["saving_s"] == 10.0
    assert result["verdict"] == "no_change"  # under 5%


def test_regression_when_proposed_slower():
    result = compare_runs(_run(600.0), _run(700.0))
    assert result["saving_s"] == -100.0
    assert result["verdict"] == "regression"


def test_failure_overrides_timing():
    # Even a faster proposed run is a failure if it did not pass.
    result = compare_runs(_run(600.0), _run(120.0, conclusion="failure"))
    assert result["proposed_passed"] is False
    assert result["verdict"] == "failure"


def test_baseline_failure_is_inconclusive():
    # A failed baseline is not a valid timing reference, so even a passing,
    # faster proposed run cannot be scored -- the verdict is inconclusive.
    result = compare_runs(_run(38.0, conclusion="failure"), _run(300.0))
    assert result["baseline_passed"] is False
    assert result["verdict"] == "inconclusive"


def test_both_failed_is_inconclusive():
    # Baseline failure takes precedence: a pre-existing breakage in both runs
    # can't be attributed to the change, so it is inconclusive, not failure.
    result = compare_runs(
        _run(600.0, conclusion="failure"), _run(120.0, conclusion="failure")
    )
    assert result["baseline_passed"] is False
    assert result["proposed_passed"] is False
    assert result["verdict"] == "inconclusive"


def test_duration_computed_from_timestamps():
    baseline = {
        "run_started_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:10:00Z",
        "conclusion": "success",
    }
    proposed = {
        "run_started_at": "2024-01-01T00:00:00Z",
        "updated_at": "2024-01-01T00:05:00Z",
        "conclusion": "success",
    }
    result = compare_runs(baseline, proposed)
    assert result["baseline_duration_s"] == 600.0
    assert result["proposed_duration_s"] == 300.0
    assert result["verdict"] == "speedup"
