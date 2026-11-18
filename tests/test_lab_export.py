"""`benchctl export lab-report` (issue 614): the run's report files, the anonymizer, one message per condition, the contract schema.
Hermetic: a fixture lab and a hand-made run folder; nothing talks to a cluster."""
import json
from pathlib import Path

import jsonschema
import pytest
from typer.testing import CliRunner

from benchctl import cli, contract, lab, lab_export, lab_run

FIX = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "lab" / "full"
SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/agents/schemas/export-lab-report.json").read_text())
runner = CliRunner()


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    monkeypatch.setattr(lab, "ROOT", r)
    return r


def make_run(root, rid="20260101-000000", text="slow on gpu001 and cpu002, user u1"):
    f = lab_run.reports_dir("prod-a", root) / rid
    f.mkdir(parents=True)
    doc = {"name": "prod-a", "run": rid, "verdict": "Ship", "comparable": True, "single_run": True, "factor": 1.0, "findings": {"hard": [text]},
           "thresholds": {}, "a": None, "b": {}, "suggestions": [{"text": text}], "time_scaled": [], "not_measured": ["x"]}
    (f / "report.json").write_text(json.dumps(doc))
    (f / "report.md").write_text(f"# report\n\n- {text}\n")
    return rid


def test_plain_export_is_the_files_unchanged(root):
    rid = make_run(root)
    doc, md = lab_export.export("prod-a", rid, root=root)
    assert doc["verdict"] == "Ship" and "gpu001" in md and "anonymized" not in doc


def test_anonymize_maps_nodes_and_users_everywhere(root):
    rid = make_run(root)
    names = lab_export.node_map("prod-a", root)
    doc, md = lab_export.export("prod-a", "last", anonymize=True, root=root)
    blob = json.dumps(doc) + md
    assert "gpu001" not in blob and "cpu002" not in blob and doc["anonymized"] is True
    assert names["gpu001"] in blob and names["cpu002"] in blob and " u1" not in blob
    assert sorted(names.values()) == [f"lab-node-{i:03d}" for i in range(1, len(names) + 1)]
    assert rid in blob


def test_a_name_inside_a_longer_word_is_left_alone(root):
    make_run(root, text="gpu0011 and xgpu001 and gpu001")
    doc, _ = lab_export.export("prod-a", "last", anonymize=True, root=root)
    s = doc["findings"]["hard"][0]
    assert s.startswith("gpu0011 and xgpu001 and lab-node-")


@pytest.mark.parametrize("name,run,msg", [("nolab", "last", "no lab nolab"), ("prod-a", "last", "no run yet"), ("prod-a", "nope", "no run nope"),
                                          ("prod-a", "../x", "no run")])
def test_one_message_per_condition(root, name, run, msg):
    with pytest.raises(lab_export.ExportError, match=msg):
        lab_export.export(name, run, root=root)


def test_a_run_without_a_report_is_named(root):
    (lab_run.reports_dir("prod-a", root) / "r1").mkdir(parents=True)
    with pytest.raises(lab_export.ExportError, match="has no report"):
        lab_export.export("prod-a", "r1", root=root)


def test_command_json_text_file_and_errors(root, tmp_path):
    rid = make_run(root)
    r = runner.invoke(cli.app, ["export", "lab-report", "prod-a", "--json", "--anonymize"])
    data = json.loads(r.stdout)
    jsonschema.validate(data, SCHEMA, cls=jsonschema.Draft202012Validator)
    assert r.exit_code == 0 and data["contract_version"] == contract.CONTRACT_VERSION and data["anonymized"] and data["run"] == rid
    assert "# report" in runner.invoke(cli.app, ["export", "lab-report", "prod-a", rid]).stdout
    out = tmp_path / "r.md"
    assert runner.invoke(cli.app, ["export", "lab-report", "prod-a", rid, "-o", str(out)]).exit_code == 0 and out.read_text().startswith("# report")
    bad = runner.invoke(cli.app, ["export", "lab-report", "prod-a", "nope"])
    assert bad.exit_code == 1 and "no run nope" in bad.output
