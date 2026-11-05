"""jira-mock against recorded real Jira Cloud response shapes (SPEC NF-2, issue 229).

Fixtures are written by scripts/record_jira_shapes.py (shapes only: keys and types, no values). With none recorded this module
checks only the comparison itself; the contract tests skip and say how to record. Differences found are listed, each with a reason,
in tests/fixtures/jira/known-gaps.yaml and in ADR 0005; a gap that stops occurring must be removed from the list.
"""
import importlib.util
import json
import sys
import threading
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "jira"
sys.path.insert(0, str(ROOT / "scripts"))
from jira_shapes import compare, merge, shape

spec = importlib.util.spec_from_file_location("jira", ROOT / "components/jira-mock/jira.py")
jira = importlib.util.module_from_spec(spec)
spec.loader.exec_module(jira)

RECORDED = sorted((FIXTURES / "cloud").glob("*.json"))
GAPS = yaml.safe_load((FIXTURES / "known-gaps.yaml").read_text()) or []


# ---- the comparison itself (synthetic data, always runs) ----

def test_shape_keeps_keys_and_types_never_values():
    s = shape({"key": "OPS-1", "n": 3, "ok": True, "who": None, "tags": ["a", "b"], "fields": {"summary": "secret text"}})
    assert s == {"key": "string", "n": "number", "ok": "boolean", "who": "null", "tags": ["string"], "fields": {"summary": "string"}}
    assert "secret" not in json.dumps(s) and "OPS-1" not in json.dumps(s)


def test_merge_marks_keys_missing_or_null_in_some_items_as_optional():
    s = shape([{"a": {"k": 1}, "b": "x"}, {"a": None, "b": "y", "c": 3}])
    assert compare(s, [{"b": "z"}]) == []  # a and c may be absent
    assert compare(s, [{"a": {"k": 2}, "b": "z", "c": 1}]) == []
    assert compare(s, [{"a": {"k": "wrong"}, "b": "z"}]) == ["[]/a/k: real has number, mock has string"]
    assert merge([shape({"x": 1}), shape({"x": "s"})]) == {"x": "number|string"}


def test_compare_reports_missing_keys_and_wrong_types_and_ignores_extras():
    real = shape({"a": 1, "b": {"c": "x"}, "d": [{"e": True}]})
    assert compare(real, {"a": 2, "b": {"c": "y", "extra": 1}, "d": [{"e": False}], "more": 1}) == []
    assert compare(real, {"a": "no", "b": {}, "d": []}) == ["/a: real has number, mock has string", "/b/c: missing in mock"]
    assert compare(real, {"a": 1, "b": 5, "d": {}}) == ["/b: real has an object, mock has number", "/d: real has a list, mock has object"]
    assert compare(shape({"x": None}), {"x": {"any": 1}}) == []  # null in the real answer says nothing about the type


# ---- the contract (needs recorded fixtures) ----

@pytest.fixture(scope="module")
def mock():
    j = jira.Jira(deliver=lambda *a: None, sleep=lambda s: None)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), jira.make_handler(j))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    req = Request(base + "/rest/api/2/issue", method="POST", data=json.dumps(
        {"fields": {"project": {"key": "OPS"}, "summary": "GPU down", "labels": ["gpu"]}}).encode())
    with urlopen(req, timeout=5) as r:
        key = json.loads(r.read())["key"]
    yield base, {"project": "OPS", "issue": key}
    srv.shutdown()


def fill(value, names):
    if isinstance(value, str):
        return value.format(**names)
    if isinstance(value, dict):
        return {k: fill(v, names) for k, v in value.items()}
    return [fill(v, names) for v in value] if isinstance(value, list) else value


def ask(base, request, names):
    body = fill(request.get("body"), names)
    req = Request(base + fill(request["path"], names), method=request["method"], data=None if body is None else json.dumps(body).encode())
    try:
        with urlopen(req, timeout=5) as r:
            raw, code = r.read(), r.status
    except HTTPError as e:
        raw, code = e.read(), e.code
    return code, json.loads(raw) if raw else None


def gaps_for(name):
    return [g for g in GAPS if g["fixture"] == name]


def covered(problem, name):
    return any(problem.startswith(g["path"]) for g in gaps_for(name))


@pytest.mark.skipif(not RECORDED, reason="no recorded fixtures: python scripts/record_jira_shapes.py --project KEY (see docs/guides/jira-contract.md)")
@pytest.mark.parametrize("path", RECORDED, ids=lambda p: p.stem)
def test_mock_covers_the_shape_of_the_real_response(path, mock):
    base, names = mock
    fixture = json.loads(path.read_text())
    code, data = ask(base, fixture["request"], names)
    problems = [] if code == fixture["status"] else [f"status: real {fixture['status']}, mock {code}"]
    problems += compare(fixture["shape"], data)
    new = [p for p in problems if not covered(p, path.stem)]
    assert not new, f"{path.stem} drifted from the real response: {new} (fix the mock, or list the gap with its reason in known-gaps.yaml)"
    stale = [g for g in gaps_for(path.stem) if not any(p.startswith(g["path"]) for p in problems)]
    assert not stale, f"gaps that no longer occur, remove them from known-gaps.yaml: {stale}"


def test_every_listed_gap_has_a_reason_and_a_fixture_name():
    for g in GAPS:
        assert {"fixture", "path", "reason"} <= set(g) and g["reason"].strip(), g


def test_fixtures_hold_no_values():
    """Shapes only: every leaf is a type name, and nothing looks like a site, an email or a token."""
    allowed = {"string", "number", "boolean", "null", "mixed"}

    def leaves(node):
        if isinstance(node, dict):
            for v in node.values():
                yield from leaves(v)
        elif isinstance(node, list):
            for v in node:
                yield from leaves(v)
        else:
            yield node

    for path in RECORDED:
        text = path.read_text().lower()
        assert "atlassian.net" not in text and "@" not in text, path
        for leaf in leaves(json.loads(path.read_text())["shape"]):
            assert set(leaf.split("|")) <= allowed, (path, leaf)
