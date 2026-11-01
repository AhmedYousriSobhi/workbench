"""Managing the NHC checks (issue 170, ADR 0019): the module, the CLI verbs and the page's API share one validated path."""
import json
import os
import shutil
import subprocess
import threading
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError

import pytest
from typer.testing import CliRunner

from benchctl import nhc
from benchctl.cli import app

ROOT = Path(__file__).resolve().parent.parent
cli = CliRunner()


@pytest.fixture
def src(tmp_path):
    dst = tmp_path / "nhc.d"
    shutil.copytree(nhc.SRC if nhc.SRC.is_dir() else ROOT / nhc.SRC, dst)
    return dst


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A cwd that holds its own images/node-base/nhc.d and compose/generated, so the CLI never touches the checkout."""
    shutil.copytree(ROOT / "images" / "node-base" / "nhc.d", tmp_path / "images" / "node-base" / "nhc.d")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def names(src):
    return [c.name for c in nhc.load(src)]


def test_the_shipped_checks_are_valid_and_in_the_original_order(src):
    assert nhc.problems(src) == []
    assert names(src) == ["gpu-count", "gpu-xid", "ib-ports", "nic-flap"]
    assert [t["name"] for t in nhc.templates(src)] == ["metric-above", "metric-below"]


def test_a_template_check_is_added_with_its_parameters_at_a_position(src):
    nhc.add("gpu-hot", template="metric-above", params={"metric": "DCGM_FI_DEV_GPU_TEMP", "max": "85"}, position=1, src=src)
    assert names(src)[0] == "gpu-hot"
    assert "gpu-hot on metric=DCGM_FI_DEV_GPU_TEMP max=85" in (src / "checks.conf").read_text()
    check = nhc.load(src)[0]
    assert check.template == "metric-above" and check.declared == ["metric", "max"]


def test_a_template_check_really_drains_on_its_condition(src, tmp_path):
    nhc.add("gpu-hot", template="metric-above", params={"metric": "DCGM_FI_DEV_GPU_TEMP", "max": "85"}, src=src)
    m, log, stub = tmp_path / "m.txt", tmp_path / "calls", tmp_path / "scontrol"
    m.write_text('DCGM_FI_DEV_GPU_TEMP{node="gpu001",gpu="0",Hostname="gpu001"} 91.0\n')
    stub.write_text(f'#!/bin/sh\nif [ "$1" != show ]; then echo "$@" >> {log}; fi\n')
    stub.chmod(0o755)
    env = {**os.environ, "NHC_METRICS_FILE": str(m), "NHC_SCONTROL": str(stub), "NHC_NODE": "gpu001", "NHC_EXPECTED_GPUS": "0",
           "NHC_EXPECTED_IB": "0", "NHC_STATE_DIR": str(tmp_path), "NHC_CHECKS_DIR": str(src), "NHC_ENV_FILE": "/nonexistent"}
    r = subprocess.run(["bash", str(nhc.RUNNER)], env=env, capture_output=True, text=True, timeout=10, check=False)
    assert r.returncode == 1 and "reason=NHC: DCGM_FI_DEV_GPU_TEMP 91.0 above 85" in log.read_text()


def test_a_custom_script_is_added_and_a_disabled_check_does_not_run(src):
    nhc.add("always-fine", script="# check: example\n[ -n \"$node\" ] || add 'no node'\n", src=src)
    assert nhc.load(src)[-1].description == "example"
    nhc.set_check("gpu-xid", enabled=False, src=src)
    assert [c.enabled for c in nhc.load(src)][:2] == [True, False]


@pytest.mark.parametrize("script, why", [
    ("if then\n", "syntax error"),
    ("# only a comment\n", "only comments"),
    ("add 'always unhealthy'\n", "healthy sample metrics"),
    ("scontrol update nodename=x state=drain\n", "must not call scontrol"),
    ("sleep 60\n", "10 s or longer"),
])
def test_a_bad_script_is_refused_and_nothing_changes(src, script, why):
    before = {p.name: p.read_bytes() for p in src.glob("*") if p.is_file()}
    with pytest.raises(nhc.NhcError, match=why):
        nhc.add("bad", script=script, src=src)
    assert {p.name: p.read_bytes() for p in src.glob("*") if p.is_file()} == before


@pytest.mark.parametrize("kwargs, why", [
    ({"template": "metric-above"}, "needs max, metric"),
    ({"template": "metric-above", "params": {"metric": "x", "max": "1", "extra": "2"}}, "does not take extra"),
    ({"template": "metric-above", "params": {"metric": "a b", "max": "1"}}, "no spaces"),
    ({"template": "nope"}, "no template"),
    ({"template": "metric-above", "script": "x"}, "exactly one"),
    ({}, "exactly one"),
])
def test_parameter_and_template_mistakes_are_named(src, kwargs, why):
    with pytest.raises(nhc.NhcError, match=why):
        nhc.add("new-check", src=src, **kwargs)


def test_names_must_be_safe_and_unique(src):
    for bad in ("Bad", "a b", "../x", "1x", ""):
        with pytest.raises(nhc.NhcError, match="not a check name"):
            nhc.add(bad, script="true\n", src=src)
    with pytest.raises(nhc.NhcError, match="exists"):
        nhc.add("gpu-xid", script="true\n", src=src)


def test_set_params_move_remove_and_reorder(src):
    nhc.add("gpu-hot", template="metric-above", params={"metric": "m", "max": "1"}, src=src)
    nhc.set_check("gpu-hot", params={"max": "2"}, position=1, src=src)
    assert nhc.load(src)[0].params == {"metric": "m", "max": "2"}
    nhc.reorder(["gpu-xid", "gpu-hot", "gpu-count", "ib-ports", "nic-flap"], src)
    assert names(src)[:2] == ["gpu-xid", "gpu-hot"]
    with pytest.raises(nhc.NhcError, match="every check exactly once"):
        nhc.reorder(["gpu-xid"], src)
    nhc.remove("gpu-hot", src)
    assert not (src / "gpu-hot.sh").exists() and "gpu-hot" not in names(src)
    with pytest.raises(nhc.NhcError, match="no check named"):
        nhc.remove("gpu-hot", src)


def test_a_hand_edit_is_checked_line_by_line(src):
    conf = src / "checks.conf"
    for text, why in [("gpu-xid maybe\n", "'on' or 'off'"), ("gpu-xid on\ngpu-xid on\n", "twice"), ("gpu-xid on max\n", "not key=value"),
                      ("Gpu on\n", "not a check name"), ("ghost on\n", "ghost.sh does not exist")]:
        conf.write_text(text)
        assert why in " ".join(nhc.problems(src)), text
    conf.write_text("gpu-xid on\n")
    (src / "stray.sh").write_text("true\n")
    assert "stray.sh is not listed" in " ".join(nhc.problems(src))
    (src / "gpu-xid.sh").write_text("echo ${\n")
    assert "syntax error" in " ".join(nhc.problems(src))


def test_sync_copies_only_valid_sets_and_drops_stale_files(src, tmp_path):
    out = tmp_path / "out"
    assert nhc.sync(src, out) == []
    assert {p.name for p in out.iterdir()} == {"checks.conf", "gpu-count.sh", "gpu-xid.sh", "ib-ports.sh", "nic-flap.sh"}
    (out / "old.sh").write_text("x")
    (src / "checks.conf").write_text("gpu-xid on\nbroken line here\n")
    assert nhc.sync(src, out)  # refused: the delivered copy stays as it was
    assert (out / "old.sh").exists() and "gpu-count" in (out / "checks.conf").read_text()
    (src / "checks.conf").write_text((out / "checks.conf").read_text())
    assert nhc.sync(src, out) == [] and not (out / "old.sh").exists()


def test_cli_add_set_get_remove_and_the_delivered_copy(repo):
    r = cli.invoke(app, ["add", "check", "gpu-hot", "--template", "metric-above", "--set", "metric=DCGM_FI_DEV_GPU_TEMP", "--set", "max=85", "--position", "1"])
    assert r.exit_code == 0 and "added check gpu-hot" in r.output
    assert (repo / "compose/generated/nhc/gpu-hot.sh").is_file()
    rows = json.loads(cli.invoke(app, ["get", "checks", "-o", "json"]).output)
    assert rows[0]["name"] == "gpu-hot" and rows[0]["params"] == "metric=DCGM_FI_DEV_GPU_TEMP max=85" and rows[0]["position"] == 1
    assert cli.invoke(app, ["set", "check", "gpu-xid", "off"]).exit_code == 0
    assert "gpu-xid off" in (repo / "compose/generated/nhc/checks.conf").read_text()
    assert cli.invoke(app, ["set", "check", "gpu-hot", "--set", "max=90"]).exit_code == 0
    assert cli.invoke(app, ["remove", "check", "gpu-hot"]).exit_code == 0
    assert not (repo / "compose/generated/nhc/gpu-hot.sh").exists()
    assert cli.invoke(app, ["validate", "checks"]).output.startswith("ok: 4 checks")


def test_cli_refusals_exit_1_say_nothing_changed_and_usage_errors_exit_2(repo):
    script = repo / "mine.sh"
    script.write_text("add 'always'\n")
    r = cli.invoke(app, ["add", "check", "mine", "--script", str(script)])
    assert r.exit_code == 1 and "nothing changed" in r.output and "healthy sample metrics" in r.output
    assert cli.invoke(app, ["add", "check", "mine"]).exit_code == 2
    assert cli.invoke(app, ["add", "check", "mine", "--script", str(script), "--template", "metric-above"]).exit_code == 2
    assert cli.invoke(app, ["add", "check", "mine", "--script", str(repo / "absent.sh")]).exit_code == 1
    assert cli.invoke(app, ["set", "check", "gpu-xid"]).exit_code == 2
    assert cli.invoke(app, ["set", "check", "gpu-xid", "maybe"]).exit_code == 2
    assert cli.invoke(app, ["set", "check", "ghost", "off"]).exit_code == 1


def test_cli_reload_after_a_hand_edit_validates_before_delivering(repo):
    conf = repo / "images/node-base/nhc.d/checks.conf"
    conf.write_text(conf.read_text().replace("gpu-xid on", "gpu-xid off"))
    assert cli.invoke(app, ["reload", "checks"]).exit_code == 0
    assert "gpu-xid off" in (repo / "compose/generated/nhc/checks.conf").read_text()
    (repo / "images/node-base/nhc.d/gpu-xid.sh").write_text("if then\n")
    r = cli.invoke(app, ["reload", "checks"])
    assert r.exit_code == 1 and "syntax error" in r.output
    assert cli.invoke(app, ["validate", "checks"]).exit_code == 1


def test_completion_names_checks_and_templates(repo):
    from benchctl import completion

    assert completion.checks("gpu-") == ["gpu-count", "gpu-xid"] and completion.check_templates("metric-a") == ["metric-above"]


def test_compose_write_delivers_the_checks_the_nodes_mount(repo):
    import yaml

    from benchctl import compose

    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    prof = {"groups": ["core", "access"], "name": "t"}
    data = compose.generate(sim, prof)
    assert any("./nhc:/etc/workbench/nhc:ro" in s.get("volumes", []) for s in data["services"].values() if s["labels"].get("workbench.node"))
    compose.write(data, out=repo / "compose/generated/docker-compose.yml", sim=sim)
    assert (repo / "compose/generated/nhc/checks.conf").is_file()


# --- the page's API -------------------------------------------------------------------------------------------------

pytest.importorskip("starlette")
pytest.importorskip("uvicorn")
import uvicorn

from tests.test_webui import TOKEN, free_port, no_stack
from webui.app import create_app


@pytest.fixture
def page(src, tmp_path):
    out = tmp_path / "out"
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(create_app(TOKEN, str(tmp_path / "gov.sock"), tmp_path, ROOT / "sim.yaml", fetch=no_stack, poster=no_stack,
                                                      nhc_src=src, nhc_out=out), host="127.0.0.1", port=port, log_level="error"))
    threading.Thread(target=server.run, daemon=True).start()
    deadline = time.time() + 5
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}", out
    server.should_exit = True


def call(base, body=None, csrf=True):
    headers = {"Authorization": f"Bearer {TOKEN}", **({"X-Requested-With": "workbench"} if csrf else {})}
    r = urllib.request.Request(base + "/api/nhc", method="POST" if body is not None else "GET", headers=headers,
                               data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(r, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except HTTPError as e:
        return e.code, json.loads(e.read())


def test_the_page_lists_reorders_toggles_adds_and_removes(page):
    base, out = page
    code, state = call(base)
    assert code == 200 and state["checks"][0]["name"] == "gpu-count" and state["problems"] == []
    assert [t["name"] for t in state["templates"]] == ["metric-above", "metric-below"]
    order = ["nic-flap", "gpu-count", "gpu-xid", "ib-ports"]
    assert [c["name"] for c in call(base, {"op": "reorder", "order": order})[1]["checks"]] == order
    assert (out / "checks.conf").read_text().splitlines()[3].startswith("nic-flap")  # delivered to the nodes' mount at once
    assert call(base, {"op": "toggle", "name": "gpu-xid", "enabled": False})[1]["checks"][2]["enabled"] is False
    add = {"op": "add", "template": "metric-below", "name": "cold", "params": {"metric": "m", "min": "5"}, "position": 2}
    assert [c["name"] for c in call(base, add)[1]["checks"]][1] == "cold"
    assert call(base, {"op": "params", "name": "cold", "params": {"min": "7"}})[1]["checks"][1]["params"]["min"] == "7"
    assert "cold" not in [c["name"] for c in call(base, {"op": "remove", "name": "cold"})[1]["checks"]]


def test_the_page_cannot_add_a_script_and_a_refusal_changes_nothing(page):
    base, _ = page
    before = call(base)[1]
    code, body = call(base, {"op": "add", "name": "mine", "script": "add boom", "template": "nope"})
    assert code == 422 and "no template" in body["detail"]
    assert call(base, {"op": "add", "template": "metric-above", "name": "x", "params": {"metric": "m"}})[0] == 422
    assert call(base, {"op": "reorder", "order": ["gpu-xid"]})[0] == 422
    assert call(base, {"op": "explode"})[0] == 422
    assert call(base)[1] == before


def test_page_writes_need_the_csrf_header_and_the_token(page):
    base, _ = page
    assert call(base, {"op": "remove", "name": "gpu-xid"}, csrf=False)[0] == 403
    r = urllib.request.Request(base + "/api/nhc", method="POST", headers={"X-Requested-With": "workbench"}, data=b"{}")
    with pytest.raises(HTTPError) as e:
        urllib.request.urlopen(r, timeout=5)
    assert e.value.code == 401
