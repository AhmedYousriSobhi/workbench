import subprocess
from pathlib import Path

import pytest
import yaml

from simctl import stack


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_stages_ordered():
    got = stack.stage_services(["cn001", "slurmrestd", "slurmctld", "grafana", "bmc-farm", "weird"])
    assert [g[0] for g in got] == ["observability", "slurm core", "slurm services", "mocks", "nodes", "other"]


def test_governor_admission_refusal_and_absent_governor():
    msgs = []
    assert not stack.governor_admits(500, msgs.append, ask=lambda r: {"ok": False, "error": "refused: L2"})
    assert "refused: L2" in msgs[0]
    assert stack.governor_admits(500, msgs.append, ask=lambda r: {"ok": True})

    def down(r):
        raise FileNotFoundError

    assert stack.governor_admits(500, msgs.append, ask=down) and msgs[-1].startswith("WARN")


def test_refused_when_it_does_not_fit():
    calls, msgs = [], []
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    assert not stack.up(sim, "min", runner=lambda c: calls.append(c) or cp(), say=msgs.append, available=1 * 1024**3)
    assert not any("up" in c for c in calls) and msgs[0].startswith("REFUSED")  # only read-only `ps`


def test_up_starts_in_order_and_waits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    import shutil
    root = Path(__file__).resolve().parents[1]
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    started: list[str] = []

    def runner(cmd):
        if "up" in cmd:
            started.extend(cmd[cmd.index("--no-deps") + 1:])
        if "ps" in cmd:
            return cp(out="\n".join(started))
        return cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    monkeypatch.setattr(stack, "provision_token", lambda: tmp_path / "t")
    assert stack.up(sim, "min", runner=runner, say=lambda m: None, available=12 * 1024**3, wait=1)
    assert started.index("prometheus") < started.index("slurmctld") < started.index("cn001")


def test_down_reports_failure():
    assert not stack.down(runner=lambda c: cp(1, err="boom"), say=lambda m: None)


def test_token_created_reused_and_replaced_when_stale(tmp_path):
    calls = []

    def http(method, path, body, url, auth):
        calls.append(path)
        return {"id": 7} if path.endswith("serviceaccounts") else {"key": f"glsa_{len(calls)}"}

    f = tmp_path / "s" / "tok"
    assert stack.provision_token(path=f, http=http, valid=lambda t, u: True) == f
    assert f.read_text() == "glsa_2" and oct(f.stat().st_mode)[-3:] == "600"
    stack.provision_token(path=f, http=http, valid=lambda t, u: True)
    assert len(calls) == 2  # valid token is reused
    stack.provision_token(path=f, http=http, valid=lambda t, u: False)  # new Grafana: stale token
    assert f.read_text() == "glsa_4" and oct(f.stat().st_mode)[-3:] == "600"


def test_token_waits_for_grafana(tmp_path):
    n = {"i": 0}

    def http(method, path, body, url, auth):
        n["i"] += 1
        if n["i"] < 3:
            raise ConnectionRefusedError
        return {"id": 1} if path.endswith("serviceaccounts") else {"key": "k"}

    stack.provision_token(path=tmp_path / "t", http=http, valid=lambda t, u: False, sleep=lambda s: None)
    assert (tmp_path / "t").read_text() == "k"


def test_unbuildable_services_are_skipped(tmp_path):
    (tmp_path / "ok").mkdir()
    (tmp_path / "ok" / "Dockerfile").write_text("FROM scratch")
    (tmp_path / "empty").mkdir()
    svcs = {"a": {"build": "ok"}, "b": {"build": "empty"}, "c": {"image": "x"}}
    assert stack.unbuildable(svcs, tmp_path) == ["b"]


def test_scale_down_removes_containers_by_label_not_compose_file(tmp_path, monkeypatch):
    import shutil
    root = Path(__file__).resolve().parents[1]
    monkeypatch.chdir(tmp_path)
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    monkeypatch.setattr(stack, "governor_admits", lambda n, say: True)
    cmds: list[list[str]] = []

    def runner(cmd):
        cmds.append(cmd)
        if cmd[:2] == ["docker", "ps"] and "--format" in cmd:
            return cp(out="cn001\ncn002\ncn003\ncn004")
        if cmd[:2] == ["docker", "ps"]:  # -aq lookup by service label
            return cp(out="abc123")
        if "ps" in cmd:
            return cp(out="slurmctld\ncn001\ncn002")
        return cp()

    assert stack.scale(sim, "min", 2, runner=runner, say=lambda m: None)
    removed = [c for c in cmds if c[:3] == ["docker", "rm", "-f"]]
    assert len(removed) == 2  # cn003 and cn004


def test_up_on_a_running_stack_is_not_refused_for_low_memory(tmp_path, monkeypatch):
    """The running stack is already in "used" memory; counting its budget again refused a no-op."""
    monkeypatch.chdir(tmp_path)
    import shutil
    root = Path(__file__).resolve().parents[1]
    for d in ("profiles", "components", "images"):
        shutil.copytree(root / d, tmp_path / d, dirs_exist_ok=True)
    sim = yaml.safe_load((root / "sim.yaml").read_text())
    from simctl import compose
    every = "\n".join([*compose.generate(sim, compose.load_profile("min"))["services"],
                       "prometheus", "grafana", "gpu-exporter-farm"])
    said: list[str] = []

    def runner(cmd):
        return cp(out=every) if "ps" in cmd else cp()

    monkeypatch.setattr(stack, "governor_admits", lambda n, say: pytest.fail("nothing to admit"))
    monkeypatch.setattr(stack, "provision_token", lambda: tmp_path / "t")
    assert stack.up(sim, "min", runner=runner, say=said.append, available=1 * 1024**3, wait=1)
    assert any("already running" in m for m in said)
