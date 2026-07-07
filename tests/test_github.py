"""Tests for the GitHub ingestion layer.

Uses a fake ``requests.Session`` injected via monkeypatch so no real network
calls are made and no extra mocking dependency is required.
"""

import base64
import json

import pytest
import requests

from ingest import github
from ingest.github import RateLimitError


def make_response(status_code, *, json_body=None, text=None, headers=None):
    """Build a real requests.Response with the given payload."""
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    if json_body is not None:
        response._content = json.dumps(json_body).encode("utf-8")
        response.headers.setdefault("Content-Type", "application/json")
    elif text is not None:
        response._content = text.encode("utf-8")
    else:
        response._content = b""
    return response


class FakeSession:
    """Routes requests through a handler(method, url, params) -> Response."""

    def __init__(self, handler):
        self.handler = handler

    def request(self, method, url, params=None, timeout=None):
        return self.handler(method, url, params)

    def close(self):
        pass


def use_handler(monkeypatch, handler):
    monkeypatch.setattr(github, "_get_session", lambda: FakeSession(handler))


def test_fetch_workflows_success(monkeypatch):
    workflow_yaml = "name: CI\non: push\njobs:\n  build:\n    runs-on: ubuntu-latest\n"
    encoded = base64.b64encode(workflow_yaml.encode("utf-8")).decode("utf-8")

    def handler(method, url, params):
        if url.endswith("/contents/.github/workflows"):
            return make_response(
                200,
                json_body=[
                    {
                        "type": "file",
                        "name": "ci.yml",
                        "path": ".github/workflows/ci.yml",
                    }
                ],
            )
        if url.endswith("/contents/.github/workflows/ci.yml"):
            return make_response(200, json_body={"content": encoded})
        raise AssertionError(f"unexpected url: {url}")

    use_handler(monkeypatch, handler)

    workflows = github.fetch_workflows("octocat", "hello")

    assert len(workflows) == 1
    assert workflows[0]["name"] == "ci.yml"
    assert workflows[0]["path"] == ".github/workflows/ci.yml"
    assert workflows[0]["content"]["name"] == "CI"
    assert workflows[0]["content"]["jobs"]["build"]["runs-on"] == "ubuntu-latest"
    # PyYAML parses the `on:` key as boolean True (YAML 1.1), not the string "on".
    assert workflows[0]["content"][True] == "push"


def test_fetch_workflows_missing_directory(monkeypatch):
    def handler(method, url, params):
        if url.endswith("/contents/.github/workflows"):
            return make_response(404, json_body={"message": "Not Found"})
        raise AssertionError(f"unexpected url: {url}")

    use_handler(monkeypatch, handler)

    assert github.fetch_workflows("octocat", "hello") == []


def test_rate_limit_raises(monkeypatch):
    def handler(method, url, params):
        return make_response(
            403,
            json_body={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "9999999999",
            },
        )

    use_handler(monkeypatch, handler)

    with pytest.raises(RateLimitError) as excinfo:
        github.fetch_run_history("octocat", "hello")

    assert excinfo.value.status_code == 403
    assert "Reset in" in str(excinfo.value)


def test_rate_limit_with_retry_after(monkeypatch):
    def handler(method, url, params):
        return make_response(
            429,
            json_body={"message": "Too Many Requests"},
            headers={"Retry-After": "60"},
        )

    use_handler(monkeypatch, handler)

    with pytest.raises(RateLimitError) as excinfo:
        github.fetch_run_history("octocat", "hello")

    assert "60 seconds" in str(excinfo.value)
