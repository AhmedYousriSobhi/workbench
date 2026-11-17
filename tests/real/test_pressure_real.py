"""Ladder against the live stack: levels climb and recover, /metrics and Grafana annotations follow.

The dry-run test needs only Docker and the built images (about 3 GB, slow). The root test also checks
the real effect on the containers' cgroups.
"""
import json
import os
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest
import yaml
from helpers import ask, bring_up, signals, start_governor, stop_governor, wait_for

from benchctl import stack
from governor.executors import cgroup_dir, discover_docker

ROOT = Path(__file__).resolve().parents[2]
FAST = json.dumps({"sustain_s": 0.5, "l4_sustain_s": 0.5, "calm_s": 1, "min_dwell_s": 0.3, "l5_after_s": 1000})


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.chdir(ROOT)
    s = yaml.safe_load((ROOT / "sim.yaml").read_text())
    # the test is about the ladder, not admission: skip the (pessimistic) budget pre-flight
    bring_up(s)
    yield s
    stack.down(say=lambda m: None)


def containers() -> list[str]:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=workbench"],
                       capture_output=True, text=True, check=True)
    return sorted(r.stdout.split())


def test_discovered_cgroups_exist_with_the_files_the_governor_writes(sim):
    """The governor acts through these paths; a wrong path means no throttle/freeze at all."""
    groups, cgroups = discover_docker()
    assert len(groups) >= 6 and len(cgroups) == len(groups)
    for name, cg in cgroups.items():
        for f in ("cgroup.freeze", "memory.high", "memory.reclaim", "cgroup.kill", "memory.current"):
            assert (cg / f).exists(), f"{name}: {cg / f} missing"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def scrape(port: int) -> dict[str, float]:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/metrics", timeout=5) as r:
        rows = (ln.rsplit(" ", 1) for ln in r.read().decode().splitlines() if not ln.startswith("#"))
        return {k: float(v) for k, v in rows}


def grafana_annotations(token: str) -> list[str]:
    req = urllib.request.Request("http://127.0.0.1:3000/api/annotations?tags=governor&limit=100",
                                 headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return [a["text"] for a in json.loads(r.read())]


def climb(sig: Path, sock: Path, port: int, want: int, **sig_kw) -> None:
    signals(sig, **sig_kw)
    assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] == want, timeout=30), f"never reached L{want}"
    assert wait_for(lambda: scrape(port)["workbench_governor_level"] == want, timeout=10)


def test_ladder_climbs_and_recovers_on_the_live_stack(sim, tmp_path):
    token = (ROOT / "compose/generated/secrets/grafana_token").read_text().strip()
    port = free_port()
    before = containers()
    gov, sig, sock = start_governor(
        tmp_path, "--dry-run", "--no-watchdog", "--thresholds", FAST, "--refresh-interval", "1",
        "--metrics-addr", f"127.0.0.1:{port}", "--grafana-url", "http://127.0.0.1:3000",
        env={"WORKBENCH_GRAFANA_TOKEN": token})
    try:
        # the endpoint answers with an empty body until the governor's first control step has rendered the metrics
        assert wait_for(lambda: scrape(port).get("workbench_governor_level") == 0)
        assert wait_for(lambda: "workbench-cn001-1" in ask(sock, {"cmd": "status"})["groups"])
        m = scrape(port)
        assert m['workbench_governor_groups{prio="P4"}'] >= 6  # the six nodes, discovered from Docker
        assert m["workbench_host_mem_available_bytes"] > 0

        climb(sig, sock, port, 1, avail_gb=1.8)
        climb(sig, sock, port, 2, avail_gb=4, some=25)
        climb(sig, sock, port, 3, avail_gb=4, some=35)
        audit = " ".join(ask(sock, {"cmd": "status"})["audit"])
        assert "throttle workbench-cn001-1" in audit and "freeze workbench-cn001-1" in audit
        assert scrape(port)["workbench_governor_actions_total"] > 0

        assert not ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]
        # /metrics is rendered once per control step, so counters lag by at most one interval
        assert wait_for(lambda: scrape(port)['workbench_governor_admit_total{result="refused"}'] == 1)

        climb(sig, sock, port, 0, avail_gb=8)  # calm: steps down, thaws
        assert "thaw workbench-cn001-1" in " ".join(ask(sock, {"cmd": "status"})["audit"])
        assert ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]

        assert wait_for(lambda: any("L2 -> L3" in t for t in grafana_annotations(token)), timeout=20)
    finally:
        err = stop_governor(gov)
    assert token.encode() not in err
    assert before and containers() == before  # dry-run touched nothing


def test_a_swap_storm_raises_the_ladder_and_the_live_cgroups_report_memory_events(sim, tmp_path):
    """#463: sustained swap-out adds a level on a calm host (dry run, nothing touched); memory.events of the real containers reach status and /metrics."""
    port = free_port()
    gov, sig, sock = start_governor(tmp_path, "--dry-run", "--no-watchdog", "--thresholds", json.dumps({"swap_pages_s": 100, "swap_sustain_s": 0.5, "calm_s": 1, "min_dwell_s": 0.3}),
                                    "--refresh-interval", "1", "--metrics-addr", f"127.0.0.1:{port}")
    try:
        assert wait_for(lambda: "workbench-cn001-1" in ask(sock, {"cmd": "status"})["groups"])
        climb(sig, sock, port, 2, avail_gb=8, swap=5000)
        assert ask(sock, {"cmd": "status"})["swap_out_rate"] == 5000
        climb(sig, sock, port, 0, avail_gb=8)
        assert wait_for(lambda: set(ask(sock, {"cmd": "status"})["memory_events"]) == {"high", "oom", "oom_kill"}, timeout=15)
        assert wait_for(lambda: 'workbench_container_memory_events{name="workbench-cn001-1",event="oom_kill"}' in "".join(scrape(port)), timeout=15)
    finally:
        stop_governor(gov)


@pytest.mark.skipif(os.geteuid() != 0, reason="needs root to write container cgroups")
def test_real_freeze_and_thaw_of_live_containers(sim, tmp_path):
    def frozen(name: str) -> bool:
        info = json.loads(subprocess.run(["docker", "inspect", name], capture_output=True, text=True,
                                         check=True).stdout)[0]
        events = cgroup_dir(info) / "cgroup.events"  # the same lookup the governor uses
        return "frozen 1" in events.read_text()

    gov, sig, sock = start_governor(tmp_path, "--no-watchdog", "--thresholds", FAST,
                                    "--refresh-interval", "1")
    try:
        assert wait_for(lambda: "workbench-cn001-1" in ask(sock, {"cmd": "status"})["groups"])
        signals(sig, avail_gb=4, some=35)
        assert wait_for(lambda: frozen("workbench-cn001-1"), timeout=30)
        assert not frozen("workbench-slurmctld-1")  # P1 is never frozen at L3
        signals(sig, avail_gb=8)
        assert wait_for(lambda: not frozen("workbench-cn001-1"), timeout=30)
    finally:
        stop_governor(gov)


def test_l1_pause_flag_reaches_the_login_node_and_the_jira_mock_and_goes_on_calm(sim, tmp_path):
    """#476: stop-arrivals is a flag the containers can see (a read-only mount); calm removes it. Only L1 runs: no pause, no throttle."""
    def seen(container: str) -> bool:
        r = subprocess.run(["docker", "exec", container, "test", "-e", "/run/workbench-flags/arrivals.paused"], capture_output=True, check=False)
        return r.returncode == 0

    def writable(container: str) -> bool:
        r = subprocess.run(["docker", "exec", container, "sh", "-c", "touch /run/workbench-flags/x"], capture_output=True, check=False)
        return r.returncode == 0

    gov, sig, sock = start_governor(tmp_path, "--no-watchdog", "--user-mode", "--workdir", str(ROOT), "--thresholds", FAST, "--refresh-interval", "1")
    try:
        assert wait_for(lambda: "workbench-login-1" in ask(sock, {"cmd": "status"})["groups"])
        assert not seen("workbench-login-1")
        signals(sig, avail_gb=1.9, some=11)  # L1 only: below 2 GB, above the L2 line
        assert wait_for(lambda: seen("workbench-login-1"), timeout=30)
        assert not writable("workbench-login-1")  # read-only from inside
        signals(sig, avail_gb=8)
        assert wait_for(lambda: not seen("workbench-login-1"), timeout=30)  # de-escalation resumes arrivals (FAST thresholds: calm_s is 1, the default 60 s would outlast the wait)
    finally:
        stop_governor(gov)


def test_lab_run_pauses_at_l2_and_aborts_at_l3(sim, tmp_path):
    """Issue 554: the governor's run thread writes flags/lab.paused at L2 (not at L1) and flags/lab.abort at L3, and the run ends
    aborted "under memory pressure". A candidate-only run of the fixture lab (no snapshot to restore). Written, not yet run live."""
    import shutil

    from benchctl import lab, lab_candidate
    name = "pressure-run"
    flags = ROOT / "compose" / "generated" / "flags"
    lab.import_lab(name, ROOT / "tests" / "fixtures" / "lab" / "full", replace=True, sim=sim)
    lab_candidate.init(name, replace=True)
    gov, sig, sock = start_governor(tmp_path, "--no-watchdog", "--user-mode", "--workdir", str(ROOT), "--thresholds", FAST)

    def lab_block() -> dict:
        return ask(sock, {"cmd": "status"})["lab"] or {}
    try:
        r = ask(sock, {"cmd": "lab-run", "name": name})
        if not r.get("ok") and str(r.get("error", "")).startswith("refused"):
            pytest.skip(f"ENVIRONMENT, not a code defect: {r['error']}")
        assert r["ok"], r
        assert wait_for(lambda: lab_block().get("run") == r["run"])
        signals(sig, avail_gb=1.9, some=11)  # L1: a lab run is not paused (that is arrivals.paused only)
        assert wait_for(lambda: ask(sock, {"cmd": "status"})["level"] == 1)
        assert not wait_for(lambda: (flags / "lab.paused").exists(), timeout=3)
        signals(sig, avail_gb=8, some=25)  # L2: paused
        assert wait_for(lambda: (flags / "lab.paused").exists(), timeout=30)
        signals(sig, avail_gb=8, some=35)  # L3: aborted
        assert wait_for(lambda: (flags / "lab.abort").exists() and "memory pressure" in (flags / "lab.abort").read_text(), timeout=30)
        signals(sig, avail_gb=8)
        assert wait_for(lambda: not lab_block().get("run"), timeout=300)  # the run's child ended
        prog = json.loads((lab.path(name) / "reports" / r["run"] / "progress.json").read_text())
        assert prog["state"] == "aborted" and "memory pressure" in prog["reason"]
        assert not (flags / "lab.paused").exists()
    finally:
        stop_governor(gov)
        subprocess.run([str(ROOT / "scripts" / "benchctl"), "remove", "lab", name, "--execute"], cwd=ROOT, capture_output=True, check=False)
        shutil.rmtree(lab.path(name), ignore_errors=True)
        (flags / "lab.abort").unlink(missing_ok=True)


def test_lab_run_answers_at_once_refuses_down_and_recovers_after_a_governor_restart(sim, tmp_path):
    """Issue 554, owner decisions round 3: `lab-run` answers `preparing` at once; `benchctl down` is refused (exit 3) while the run
    is active; a governor restarted mid-run shows `recovering` and refuses `admit` until the old run is wound up. Written, not yet run."""
    import shutil

    from benchctl import lab, lab_candidate
    name = "recover-run"
    flags = ROOT / "compose" / "generated" / "flags"
    lab.import_lab(name, ROOT / "tests" / "fixtures" / "lab" / "full", replace=True, sim=sim)
    lab_candidate.init(name, replace=True)
    gov, _sig, sock = start_governor(tmp_path, "--no-watchdog", "--user-mode", "--workdir", str(ROOT), "--thresholds", FAST)
    gov2 = None
    try:
        r = ask(sock, {"cmd": "lab-run", "name": name})
        assert r.get("state") == "preparing", r
        assert wait_for(lambda: (ask(sock, {"cmd": "status"})["lab"] or {}).get("state") not in (None, "preparing"), timeout=60)
        lb = ask(sock, {"cmd": "status"})["lab"]
        if lb.get("state") == "refused":
            pytest.skip(f"ENVIRONMENT, not a code defect: {lb.get('reason')}")
        env = {**os.environ, "WORKBENCH_GOV_SOCKET": str(sock)}
        down = subprocess.run([str(ROOT / "scripts" / "benchctl"), "down"], cwd=ROOT, env=env, capture_output=True, text=True, check=False)
        assert down.returncode == 3 and f"benchctl stop lab {name}" in down.stderr and "--force" in down.stderr
        stop_governor(gov)
        gov = None
        gov2, _sig, sock = start_governor(tmp_path / "again", "--no-watchdog", "--user-mode", "--workdir", str(ROOT), "--thresholds", FAST)
        assert wait_for(lambda: (ask(sock, {"cmd": "status"})["lab"] or {}).get("state") == "recovering", timeout=15)
        assert "being recovered" in ask(sock, {"cmd": "admit", "budget_mb": 10})["error"]
        assert wait_for(lambda: not (ask(sock, {"cmd": "status"})["lab"] or {}).get("active"), timeout=600)
    finally:
        for g in (gov, gov2):
            if g:
                stop_governor(g)
        subprocess.run([str(ROOT / "scripts" / "benchctl"), "remove", "lab", name, "--execute"], cwd=ROOT, capture_output=True, check=False)
        shutil.rmtree(lab.path(name), ignore_errors=True)
        (flags / "lab.abort").unlink(missing_ok=True)
        (ROOT / "compose" / "generated" / "lab" / "run-active.json").unlink(missing_ok=True)
