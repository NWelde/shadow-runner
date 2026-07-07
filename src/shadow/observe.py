"""Observe shadow runs: poll them to completion, then measure the difference.

Once two runs (baseline config vs proposed config) have been triggered, this
module watches them finish and turns the two run dicts into a single verdict:
how much time the change saved, and whether the proposed config still passed.
"""

from __future__ import annotations

import time
from datetime import datetime

from ingest.github import fetch_job_logs, get_run

POLL_INTERVAL_S = 30
SPEEDUP_THRESHOLD_PCT = 5.0


def poll_until_complete(
    owner: str, repo: str, run_id: str, timeout_minutes: int = 30
) -> dict:
    """Poll a run every 30s until GitHub reports it ``completed``.

    Returns the final run dict (its ``conclusion`` field holds the outcome:
    success / failure / cancelled / ...). Raises ``TimeoutError`` if the run has
    not completed within ``timeout_minutes``.
    """
    deadline = time.time() + timeout_minutes * 60
    while True:
        run = get_run(owner, repo, run_id)
        if run.get("status") == "completed":
            return run
        if time.time() >= deadline:
            raise TimeoutError(
                f"run {run_id} did not complete within {timeout_minutes} minutes"
            )
        time.sleep(POLL_INTERVAL_S)


def _parse_iso(value: str) -> datetime:
    """Parse a GitHub ISO-8601 timestamp (``...Z``) into a datetime."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def run_duration_s(run: dict) -> float:
    """Wall-clock seconds for a run.

    Uses a precomputed ``duration_s`` if present (handy for tests); otherwise
    measures from ``run_started_at`` (or ``created_at``) to ``updated_at``.
    """
    if run.get("duration_s") is not None:
        return float(run["duration_s"])
    start = run.get("run_started_at") or run.get("created_at")
    end = run.get("updated_at")
    if not start or not end:
        return 0.0
    return max(0.0, (_parse_iso(end) - _parse_iso(start)).total_seconds())


def _verdict(
    saving_s: float, saving_pct: float, baseline_passed: bool, proposed_passed: bool
) -> str:
    """Classify a comparison into inconclusive / failure / speedup / no_change / regression.

    Order matters. A failed *baseline* makes timing meaningless (a failed run
    usually aborts early, so its duration is not a real reference), so the result
    is ``inconclusive`` regardless of the proposed run -- the experiment can't
    attribute anything to the change. Only once the baseline is a valid reference
    does a failed *proposed* run count as ``failure`` (the change broke it).
    """
    if not baseline_passed:
        return "inconclusive"
    if not proposed_passed:
        return "failure"
    if saving_s > 0 and saving_pct >= SPEEDUP_THRESHOLD_PCT:
        return "speedup"
    if saving_s < 0 and abs(saving_pct) >= SPEEDUP_THRESHOLD_PCT:
        return "regression"
    return "no_change"


def compare_runs(baseline: dict, proposed: dict) -> dict:
    """Compare a baseline and proposed run into a timing/verdict summary.

    ``saving_s`` is baseline minus proposed (positive = faster); ``saving_pct``
    is that relative to the baseline. ``baseline_passed`` / ``proposed_passed``
    are true only when that run's conclusion is ``success``. The verdict is
    ``inconclusive`` if the baseline did not pass (its timing is not a valid
    reference), else ``failure`` if the proposed run did not pass, else
    ``speedup`` / ``regression`` when the change in time clears a 5% threshold,
    else ``no_change``.
    """
    baseline_s = run_duration_s(baseline)
    proposed_s = run_duration_s(proposed)
    saving_s = baseline_s - proposed_s
    saving_pct = (saving_s / baseline_s * 100) if baseline_s > 0 else 0.0
    baseline_passed = baseline.get("conclusion") == "success"
    proposed_passed = proposed.get("conclusion") == "success"
    return {
        "baseline_duration_s": baseline_s,
        "proposed_duration_s": proposed_s,
        "saving_s": saving_s,
        "saving_pct": saving_pct,
        "baseline_passed": baseline_passed,
        "proposed_passed": proposed_passed,
        "verdict": _verdict(saving_s, saving_pct, baseline_passed, proposed_passed),
    }


def read_failure_signal(owner: str, repo: str, job_id: str) -> str:
    """Return the raw log text of a job, for the agent to read on failure."""
    return fetch_job_logs(owner, repo, int(job_id))
