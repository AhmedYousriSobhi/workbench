"""The Lab workspace's backend (issue 556): the views, the report download, the governor requests and what the page may not do. Hermetic."""
import json
from pathlib import Path

import pytest
import yaml

pytest.importorskip("starlette")
from starlette.testclient import TestClient

from benchctl import lab, lab_run
from webui import app as webapp
from webui import lab as labview

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


@pytest.fixture
def root(tmp_path, monkeypatch):
    r = tmp_path / "lab"
    lab.import_lab("prod-a", FIX, root=r)
    monkeypatch.setattr(lab, "ROOT", r)
    return r


def make_report(root, rid="20260101-000000"):
    f = lab_run.reports_dir("prod-a", root) / rid
    f.mkdir(parents=True)
    (f / "run.json").write_text(json.dumps({"state": "done", "ab": True}))
    (f / "report.json").write_text(json.dumps({"name": "prod-a", "run": rid, "verdict": "Ship", "comparable": True, "b": {}}))
    (f / "report.md").write_text("# report on gpu001\n")


def test_view_of_a_fresh_lab(root):
    v = labview.view("prod-a", SIM, None, root)
    assert v["name"] == "prod-a" and v["steps"]["import"] == "done" and v["steps"]["validate"] == "todo" and v["steps"]["report"] == "todo"
    assert v["files"] and v["diff"] == [] and v["runs"] == [] and v["applied"] is None and v["plan"]["scale"]


def test_view_follows_the_governor_and_the_reports(root):
    make_report(root)
    v = labview.view("prod-a", SIM, {"name": "prod-a", "side": "candidate", "state": "replay_a"}, root)
    assert v["steps"]["apply"] == "now" and v["steps"]["replay"] == "now" and v["steps"]["report"] == "done" and v["runs"][0]["verdict"] == "Ship"
    other = labview.view("prod-a", SIM, {"name": "else", "side": "candidate"}, root)
    assert other["applied"] is None


def test_unknown_lab_and_requests(root):
    with pytest.raises(labview.LabRequestError, match="no lab"):
        labview.view("nope", SIM, None, root)
    assert labview.request("prod-a", "apply", {"side": "baseline"}) == {"cmd": "lab-apply", "name": "prod-a", "side": "baseline"}
    assert labview.request("prod-a", "run", {"ab": True, "target_hours": 1}) == {"cmd": "lab-run", "name": "prod-a", "ab": True, "target_hours": 1}
    assert labview.request("prod-a", "stop", {}) == {"cmd": "lab-stop", "name": "prod-a"}
    assert labview.request("prod-a", "restore")["cmd"] == "lab-restore"
    for bad in [("apply", {"side": "x; rm"}), ("run", {"target_hours": 99}), ("run", {"target_hours": True}), ("nuke", {})]:
        with pytest.raises(labview.LabRequestError):
            labview.request("prod-a", *bad)
    with pytest.raises(lab.LabError):
        labview.request("../x", "stop")


def api(tmp_path, monkeypatch, gov):
    sent = []
    monkeypatch.setattr(webapp.checks, "governor_request", lambda req, sock=None, timeout=5: sent.append(req) or gov)
    c = TestClient(webapp.create_app("tok", str(tmp_path / "g.sock"), ROOT / "scenarios", ROOT / "sim.yaml"))
    return c, sent


H = {"Authorization": "Bearer tok", "X-Requested-With": "workbench"}


def test_routes_need_the_token_and_writes_need_the_header(root, tmp_path, monkeypatch):
    c, sent = api(tmp_path, monkeypatch, {"ok": True, "lab": None})
    assert c.get("/api/lab").status_code == 401
    assert c.post("/api/lab/prod-a/stop", headers={"Authorization": "Bearer tok"}).status_code in (400, 403)
    assert sent == []


def test_list_detail_report_and_buttons(root, tmp_path, monkeypatch):
    make_report(root)
    c, sent = api(tmp_path, monkeypatch, {"ok": True, "lab": None})
    assert c.get("/api/lab", headers=H).json()["labs"][0]["name"] == "prod-a"
    assert c.get("/api/lab/prod-a", headers=H).json()["steps"]["report"] == "done"
    assert c.get("/api/lab/nope", headers=H).status_code == 404
    r = c.get("/api/lab/prod-a/report?anonymize=1", headers=H)
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "gpu001" not in r.text
    assert c.get("/api/lab/prod-a/report?format=json&run=nope", headers=H).status_code == 404
    assert c.post("/api/lab/prod-a/run", headers=H, json={"ab": True}).json()["started"] is True
    assert sent[-1] == {"cmd": "lab-run", "name": "prod-a", "ab": True}
    assert c.post("/api/lab/prod-a/apply", headers=H, json={"side": "bad"}).status_code == 422


def test_a_refusal_is_one_message(root, tmp_path, monkeypatch):
    c, _ = api(tmp_path, monkeypatch, {"ok": False, "error": "refused: memory level is L2"})
    r = c.post("/api/lab/prod-a/run", headers=H, json={})
    assert r.status_code == 409 and "L2" in r.json()["detail"]


def test_report_download(root):
    make_report(root)
    doc, md = labview.report("prod-a", "last", True, root)
    assert doc["anonymized"] and "gpu001" not in md and "lab-node-" in md
    with pytest.raises(labview.LabRequestError, match="no run"):
        labview.report("prod-a", "nope", False, root)
