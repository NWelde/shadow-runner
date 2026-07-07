"""Write the pitch: a human-readable report and a ready-to-paste PR body.

This is the final step. It takes the repo profile, the experiment that was run,
and the measured comparison, and asks ``gemma4:4b`` (via the ``ollama`` library)
to write up the evidence in prose specific to *this* repo — real job names, real
seconds saved — not a generic template.
"""

from __future__ import annotations

import json

from models import RepoProfile, ShadowExperiment

MODEL = "gemma3:4b"

_SYSTEM = (
    "You are a senior engineer writing up a measured CI optimisation. You ran a "
    "real experiment on GitHub Actions and have hard numbers. Write specifically "
    "about this repository — never generic boilerplate. Be concise and concrete."
)


def _facts(
    profile: RepoProfile, experiment: ShadowExperiment, comparison: dict
) -> dict:
    """Collect the numbers and names the model must ground its writing in."""
    job_names = sorted({job.name for run in profile.runs for job in run.jobs})
    return {
        "repo": f"{profile.owner}/{profile.name}",
        "jobs": job_names,
        "hypothesis": experiment.hypothesis,
        "baseline_duration_s": comparison["baseline_duration_s"],
        "proposed_duration_s": comparison["proposed_duration_s"],
        "saving_s": comparison["saving_s"],
        "saving_pct": comparison["saving_pct"],
        "proposed_passed": comparison["proposed_passed"],
        "verdict": comparison["verdict"],
    }


def _prompt(facts: dict) -> str:
    """Build the user prompt instructing a strict JSON {report, pr_body} reply."""
    return (
        "Using ONLY these measured facts, write a report and a PR description.\n\n"
        f"Facts:\n{json.dumps(facts, indent=2)}\n\n"
        "Return ONLY a JSON object with exactly two string keys: "
        '"report" (a one-page plain-text summary a maintainer can read in a '
        "minute, leading with the headline before/after time and percent saved) "
        'and "pr_body" (a GitHub PR description in markdown with a summary, the '
        "hypothesis tested, the measured evidence as a table or bullets, and "
        "whether checks still passed). No prose outside the JSON object."
    )


def _extract_json_object(text: str) -> dict:
    """Pull the ``{...}`` JSON object out of a possibly chatty model reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


def _chat(facts: dict) -> dict:
    """Send the pitch prompt to the model and parse its JSON reply.

    Imported lazily so importing this module never requires a live Ollama.
    """
    import ollama

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _prompt(facts)},
        ],
    )
    return _extract_json_object(response["message"]["content"])


def write_pitch(
    profile: RepoProfile, experiment: ShadowExperiment, comparison: dict
) -> dict:
    """Return ``{"report": str, "pr_body": str}`` for a measured experiment.

    The prompt grounds the model in the repo name, job names, baseline/proposed
    durations, seconds and percent saved, whether the proposed run passed, and
    the hypothesis tested. Falls back to a deterministic write-up if the model's
    reply cannot be parsed, so a pitch is always produced.
    """
    facts = _facts(profile, experiment, comparison)
    try:
        parsed = _chat(facts)
        report = str(parsed["report"])
        pr_body = str(parsed["pr_body"])
        return {"report": report, "pr_body": pr_body}
    except Exception:
        return _fallback_pitch(facts)


def _fallback_pitch(facts: dict) -> dict:
    """Deterministic report/PR body used when the model output is unusable."""
    headline = (
        f"{facts['repo']} CI: {facts['baseline_duration_s']:.0f}s -> "
        f"{facts['proposed_duration_s']:.0f}s "
        f"({facts['saving_s']:.0f}s, {facts['saving_pct']:.1f}% faster)"
    )
    passed = "passed" if facts["proposed_passed"] else "did NOT pass"
    report = (
        f"{headline}\n\n"
        f"Hypothesis tested: {facts['hypothesis']}\n"
        f"Jobs observed: {', '.join(facts['jobs']) or 'n/a'}\n"
        f"Result: verdict={facts['verdict']}; the proposed config {passed}.\n"
        "Evidence comes from two real GitHub Actions runs (baseline vs proposed)."
    )
    pr_body = (
        f"## Speed up CI by ~{facts['saving_pct']:.0f}%\n\n"
        f"**Hypothesis:** {facts['hypothesis']}\n\n"
        "### Measured evidence\n"
        f"- Baseline: {facts['baseline_duration_s']:.0f}s\n"
        f"- Proposed: {facts['proposed_duration_s']:.0f}s\n"
        f"- Saving: {facts['saving_s']:.0f}s ({facts['saving_pct']:.1f}%)\n"
        f"- Proposed checks {passed}\n\n"
        "These numbers are from a shadow experiment on real GitHub Actions "
        "infrastructure, not an estimate."
    )
    return {"report": report, "pr_body": pr_body}
