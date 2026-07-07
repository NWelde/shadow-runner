"""Tests for propose_yaml_change — removing one dep, preserving everything else."""

import pytest
import yaml

from shadow.runner import propose_yaml_change

INLINE_YAML = """name: CI
on: push  # trigger on every push
jobs:
  lint:
    runs-on: ubuntu-latest
  setup:
    runs-on: ubuntu-latest
  build:
    needs: [lint, setup]
    runs-on: ubuntu-latest
"""

BLOCK_YAML = """name: CI
on: push
jobs:
  build:
    runs-on: ubuntu-latest
  test:
    runs-on: ubuntu-latest
  deploy:
    needs:
      - build
      - test
    runs-on: ubuntu-latest
"""


def _needs(text, job):
    config = yaml.safe_load(text)
    return (config["jobs"][job] or {}).get("needs")


def test_removes_one_dep_from_inline_list():
    result = propose_yaml_change(INLINE_YAML, "build", "lint")
    assert _needs(result, "build") == ["setup"]  # other dep intact


def test_removes_one_dep_from_block_list():
    result = propose_yaml_change(BLOCK_YAML, "deploy", "build")
    assert _needs(result, "deploy") == ["test"]


def test_preserves_unrelated_formatting():
    result = propose_yaml_change(INLINE_YAML, "build", "lint")
    # The comment on the unrelated `on:` line must survive untouched.
    assert "on: push  # trigger on every push" in result
    assert "  setup:\n    runs-on: ubuntu-latest" in result


def test_removing_last_dep_drops_needs_inline():
    text = INLINE_YAML.replace("needs: [lint, setup]", "needs: [lint]")
    result = propose_yaml_change(text, "build", "lint")
    assert _needs(result, "build") is None  # needs line removed entirely
    assert "needs:" not in result


def test_dep_not_present_returns_unchanged():
    assert propose_yaml_change(INLINE_YAML, "build", "nonexistent") == INLINE_YAML


def test_unknown_job_raises():
    with pytest.raises(ValueError, match="not found"):
        propose_yaml_change(INLINE_YAML, "ghost", "lint")
