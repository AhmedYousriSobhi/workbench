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

from governor.executors import cgroup_dir, discover_docker
from simctl import stack

ROOT = Path(__file__).resolve().parents[2]
FAST = json.dumps({"sustain_s": 0.5, "calm_s": 1, "min_dwell_s": 0.3, "l5_after_s": 1000})


@pytest.fixture
def sim(monkeypatch):
    monkeypatch.chdir(ROOT)
    s = yaml.safe_load((ROOT / "sim.yaml").read_text())
    # the test is about the ladder, not admission: skip the (pessimistic) budget pre-flight
    bring_up(s)
    yield s
    stack.down(say=lambda m: None)


def containers() -> list[str]:
    r = subprocess.run(["docker", "ps", "-q", "--filter", "label=com.docker.compose.project=hpcsim"],
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
    assert wait_for(lambda: scrape(port)["hpcsim_governor_level"] == want, timeout=10)


def test_ladder_climbs_and_recovers_on_the_live_stack(sim, tmp_path):
    token = (ROOT / "compose/generated/secrets/grafana_token").read_text().strip()
    port = free_port()
    before = containers()
    gov, sig, sock = start_governor(
        tmp_path, "--dry-run", "--no-watchdog", "--thresholds", FAST, "--refresh-interval", "1",
        "--metrics-addr", f"127.0.0.1:{port}", "--grafana-url", "http://127.0.0.1:3000",
        env={"HPCSIM_GRAFANA_TOKEN": token})
    try:
        assert wait_for(lambda: scrape(port)["hpcsim_governor_level"] == 0)
        assert wait_for(lambda: "hpcsim-cn001-1" in ask(sock, {"cmd": "status"})["groups"])
        m = scrape(port)
        assert m['hpcsim_governor_groups{prio="P4"}'] >= 6  # the six nodes, discovered from Docker
        assert m["hpcsim_host_mem_available_bytes"] > 0

        climb(sig, sock, port, 1, avail_gb=1.8)
        climb(sig, sock, port, 2, avail_gb=4, some=25)
        climb(sig, sock, port, 3, avail_gb=4, some=35)
        audit = " ".join(ask(sock, {"cmd": "status"})["audit"])
        assert "throttle hpcsim-cn001-1" in audit and "freeze hpcsim-cn001-1" in audit
        assert scrape(port)["hpcsim_governor_actions_total"] > 0

        assert not ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]
        # /metrics is rendered once per control step, so counters lag by at most one interval
        assert wait_for(lambda: scrape(port)['hpcsim_governor_admit_total{result="refused"}'] == 1)

        climb(sig, sock, port, 0, avail_gb=8)  # calm: steps down, thaws
        assert "thaw hpcsim-cn001-1" in " ".join(ask(sock, {"cmd": "status"})["audit"])
        assert ask(sock, {"cmd": "admit", "budget_mb": 10})["ok"]

        assert wait_for(lambda: any("L2 -> L3" in t for t in grafana_annotations(token)), timeout=20)
    finally:
        err = stop_governor(gov)
    assert token.encode() not in err
    assert before and containers() == before  # dry-run touched nothing


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
        assert wait_for(lambda: "hpcsim-cn001-1" in ask(sock, {"cmd": "status"})["groups"])
        signals(sig, avail_gb=4, some=35)
        assert wait_for(lambda: frozen("hpcsim-cn001-1"), timeout=30)
        assert not frozen("hpcsim-slurmctld-1")  # P1 is never frozen at L3
        signals(sig, avail_gb=8)
        assert wait_for(lambda: not frozen("hpcsim-cn001-1"), timeout=30)
    finally:
        stop_governor(gov)
