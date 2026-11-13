"""The JSON contract (SPEC AG-3, AG-9; ADR 0062): every `--json` output validates against its schema, carries a
`contract_version`, equals `-o json`, and holds no secret value. Hermetic: the heavy parts are faked."""
import json
import re
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from benchctl import checks, cli, contract, describe, inventory

runner = CliRunner()
ROOT = Path(__file__).resolve().parent.parent
SCHEMAS = ROOT / "docs" / "agents" / "schemas"

# a few values that must never reach any output: a home path, an e-mail, a key block, a JWT, a password assignment
SECRETS = [re.compile(p) for p in (r"/home/[a-z_][a-z0-9_-]*/", r"[\w.+-]+@[\w-]+\.[a-z]{2,}", r"BEGIN [A-Z ]*PRIVATE KEY",
                                   r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}", r"(?i)(password|passwd|token)\s*[=:]\s*\S{6,}")]


@pytest.fixture(autouse=True)
def in_repo(monkeypatch):
    monkeypatch.chdir(ROOT)
    monkeypatch.setattr(inventory, "_containers", lambda runner=None: {"cn001": "running", "gpu001": "paused"})
    monkeypatch.setattr(checks, "run_all", lambda: [checks.Check("a", checks.OK, "fine"), checks.Check("b", checks.WARN, "meh", fix="do this")])
    monkeypatch.setattr(checks, "mem_available", lambda: 12 * 1024**3)

    def gov(req, socket=None):
        raise OSError("no socket")
    monkeypatch.setattr(checks, "governor_request", gov)
    monkeypatch.setattr(describe, "gather", lambda *a, **k: {"name": "cn001", "kind": "compute", "notes": [], "container": {"state": "running"}})


def schema(name):
    return json.loads((SCHEMAS / f"{name}.json").read_text())


# (schema, argv): the read verbs that exist today; `get endpoints|traffic|faults` and `scenario run` follow in step 10b
VERBS = [("get-nodes", ["get", "nodes"]), ("get-services", ["get", "services"]), ("get-profiles", ["get", "profiles"]),
         ("get-checks", ["get", "checks"]), ("get-scenarios", ["get", "scenarios"]), ("scenario-list", ["scenario", "list"]),
         ("get-workbench", ["get", "workbench"]), ("describe-node", ["describe", "node", "cn001"]), ("doctor", ["doctor"]),
         ("status", ["status"]), ("plan", ["plan", "--cpu-nodes", "2", "--gpu-nodes", "1"])]


@pytest.mark.parametrize(("name", "argv"), VERBS, ids=[v[0] for v in VERBS])
def test_json_output_matches_its_schema_and_o_json(name, argv):
    r = runner.invoke(cli.app, [*argv, "--json"])
    assert r.exit_code in (0, 1), r.output
    data = json.loads(r.stdout)
    jsonschema.validate(data, schema(name), cls=jsonschema.Draft202012Validator)
    assert data["contract_version"] == contract.CONTRACT_VERSION
    assert runner.invoke(cli.app, [*argv, "-o", "json"]).stdout == r.stdout  # `--json` is only an alias
    for pat in SECRETS:
        assert not pat.search(r.stdout), f"{name}: output matches {pat.pattern}"


def test_status_reports_a_reachable_governor(monkeypatch):
    st = {"level": 0, "dry_run": False, "mode": "user", "mem_available": 8 * 1024**3, "psi_some": 0.1, "psi_full": 0.0,
          "groups": ["p6"], "audit": ["L0 start"], "violations": []}
    monkeypatch.setattr(checks, "governor_request", lambda req, socket=None: st)
    r = runner.invoke(cli.app, ["status", "--json"])
    data = json.loads(r.stdout)
    jsonschema.validate(data, schema("status"), cls=jsonschema.Draft202012Validator)
    assert r.exit_code == 0 and data["governor"]["reachable"] is True and data["governor"]["mem_available_mb"] == 8192


def test_list_outputs_are_enveloped():
    data = json.loads(runner.invoke(cli.app, ["get", "nodes", "--json"]).stdout)
    assert {"name": "gpu001", "kind": "gpu", "state": "paused"} in data["items"]


def test_every_schema_is_valid_draft_2020_12_and_has_a_verb():
    files = {p.stem for p in SCHEMAS.glob("*.json")}
    for name in files:
        jsonschema.Draft202012Validator.check_schema(schema(name))
        assert schema(name)["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert "contract_version" in schema(name)["required"]
    assert files == {v[0] for v in VERBS}, "a schema without a test, or a verb without a schema"


def test_the_changelog_names_the_current_version():
    text = (ROOT / "docs" / "agents" / "CHANGELOG.md").read_text()
    assert f"## {contract.CONTRACT_VERSION} (" in text
