"""Triage layer: turn the structured CI signals into ranked findings via Ollama.

``build_triage_input`` (the assembly of dep graph + durations + failure rates +
critical path) already lives in ``triage.repo_profile_input``; it is re-exported
here so callers have one obvious triage entry point. This module adds the part
that reasons over those signals: it prompts a local ``gemma4:4b`` model through
the ``ollama`` library and parses the reply into validated ``Finding`` objects.
"""

from __future__ import annotations

import json
from typing import Literal, Optional

from pydantic import BaseModel, ValidationError

from triage.repo_profile_input import build_triage_input

__all__ = ["Finding", "TriageError", "build_triage_input", "run_triage"]

MODEL = "gemma3:4b"


class Finding(BaseModel):
    """One actionable observation the model made about the pipeline."""

    model_config = {"populate_by_name": True}

    job: str
    dependency_to_remove: Optional[str] = None
    problem: str
    type: Literal[
        "parallelism_opportunity", "slow_on_critical_path", "high_failure_rate"
    ]
    estimated_saving_s: float = 0.0
    confidence: Literal["high", "medium", "low"] = "medium"

    @classmethod
    def _coerce(cls, data: dict) -> dict:
        """Normalise model quirks before validation."""
        if data.get("estimated_saving_s") is None:
            data["estimated_saving_s"] = 0.0
        if data.get("confidence") not in ("high", "medium", "low"):
            data["confidence"] = "medium"
        return data


class TriageError(Exception):
    """Raised when the model's output cannot be parsed into findings twice."""


_SYSTEM = (
    "You are a CI pipeline optimisation expert. You are given a JSON object "
    "describing a GitHub Actions pipeline: its job dependency graph, average "
    "job durations in seconds, per-job failure rates as percentages, and the "
    "duration-weighted critical path. Identify the highest-impact problems."
)

_INSTRUCTIONS = (
    "Return ONLY a JSON array of finding objects. No prose, no markdown, no code "
    "fences. Each object must have exactly these keys: "
    '"job" (string, the affected job id), '
    '"dependency_to_remove" (string or null — the needs edge to drop, only for '
    "parallelism opportunities), "
    '"problem" (string, one sentence), '
    '"type" (one of "parallelism_opportunity", "slow_on_critical_path", '
    '"high_failure_rate"), '
    '"estimated_saving_s" (number, seconds saved), '
    '"confidence" (one of "high", "medium", "low"). '
    "Prefer removing an unnecessary needs edge so two jobs can run in parallel."
)


def _build_prompt(triage_input: dict, strict: bool = False) -> str:
    """Compose the user prompt; ``strict`` adds a harder no-prose reminder."""
    payload = json.dumps(triage_input, indent=2, default=str)
    prompt = f"{_INSTRUCTIONS}\n\nPipeline:\n{payload}"
    if strict:
        prompt = (
            "Your previous reply was not valid JSON. Output a raw JSON array and "
            "nothing else — the first character must be '[' and the last ']'.\n\n"
            + prompt
        )
    return prompt


def _strip_fences(text: str) -> str:
    """Best-effort extraction of the JSON array from a possibly chatty reply."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lstrip().lower().startswith("json"):
            text = text.lstrip()[4:]
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def _ask_and_parse(triage_input: dict, strict: bool) -> list[Finding]:
    """Send one prompt to the model and parse the reply into findings.

    Imported lazily so the module loads without a running Ollama service.
    Raises ``json.JSONDecodeError`` or ``pydantic.ValidationError`` on bad output.
    """
    import ollama

    response = ollama.chat(
        model=MODEL,
        messages=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": _build_prompt(triage_input, strict)},
        ],
    )
    raw = _strip_fences(response["message"]["content"])
    items = json.loads(raw)
    if not isinstance(items, list):
        raise json.JSONDecodeError("expected a JSON array", raw, 0)
    return [Finding(**Finding._coerce(dict(item))) for item in items]


def run_triage(triage_input: dict) -> list[Finding]:
    """Ask the model for findings, retrying once with a stricter prompt.

    Parses the model's JSON into validated ``Finding`` objects. If parsing or
    validation fails twice, raises ``TriageError``.
    """
    try:
        return _ask_and_parse(triage_input, strict=False)
    except (json.JSONDecodeError, ValidationError, KeyError):
        pass
    try:
        return _ask_and_parse(triage_input, strict=True)
    except (json.JSONDecodeError, ValidationError, KeyError) as exc:
        raise TriageError(f"could not parse model output into findings: {exc}") from exc
