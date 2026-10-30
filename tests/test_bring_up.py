"""tests/real/helpers.bring_up: a failed `up` says why, and a governor refusal is labelled as the environment."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "real"))
import helpers


def fake_up(lines, ok):
    def up(sim, profile, say, available):
        for line in lines:
            say(line)
        return ok
    return up


def test_success_is_silent(monkeypatch):
    monkeypatch.setattr(helpers.stack, "up", fake_up(["admitted: min"], True))
    helpers.bring_up({})


def test_governor_refusal_is_labelled_environment(monkeypatch):
    monkeypatch.setattr(helpers.stack, "up", fake_up(["admitted: min: budget 1.0 GB", "REFUSED by governor: refused: governor at L3"], False))
    with pytest.raises(AssertionError, match=r"ENVIRONMENT, not a code defect.*governor at L3"):
        helpers.bring_up({})


def test_other_failures_keep_benchctl_output(monkeypatch):
    monkeypatch.setattr(helpers.stack, "up", fake_up(["compose failed: port 8080 in use"], False))
    with pytest.raises(AssertionError, match=r"stack.up failed: compose failed: port 8080 in use") as e:
        helpers.bring_up({})
    assert "ENVIRONMENT" not in str(e.value)


def test_silent_failure_still_explains_itself(monkeypatch):
    monkeypatch.setattr(helpers.stack, "up", fake_up([], False))
    with pytest.raises(AssertionError, match="no output"):
        helpers.bring_up({})
