import subprocess
from pathlib import Path

import yaml

from simctl import stack


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_stages_ordered():
    got = stack.stage_services(["cn001", "slurmrestd", "slurmctld", "grafana", "bmc-farm", "weird"])
    assert [g[0] for g in got] == ["observability", "slurm core", "slurm services", "mocks", "nodes", "other"]


def test_refused_when_it_does_not_fit():
    calls, msgs = [], []
    sim = yaml.safe_load(Path("sim.yaml").read_text())
    assert not stack.up(sim, "min", runner=lambda c: calls.append(c) or cp(), say=msgs.append, available=1 * 1024**3)
    assert calls == [] and msgs[0].startswith("REFUSED")


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
