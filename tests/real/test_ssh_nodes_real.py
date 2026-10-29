"""sshd on the nodes (hops from the login node) and the `ssh` scenario faults, against the live min stack.

A client with Slurm on another host runs `ssh <node> <cmd>` from the login node; a client probes the login node's
banner and pins its host key. Every fault is applied by the scenario engine itself (simctl.scenario.execute).
"""
import os
import socket
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, ssh_login, wait_for

from simctl import scenario, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"


def fault(**do) -> None:
    out = scenario.execute(scenario.Step(0, "ssh", do))
    assert out.status == "ok", out.detail


def settled() -> bool:
    """sshd answers again after a config fault: it re-executes on SIGHUP and refuses connections for a moment."""
    return banner().startswith(b"SSH-")


def login_up() -> bool:
    return ssh_login("true").returncode == 0


def banner(timeout: float = 3) -> bytes:
    """What the login node says first: an SSH client waits for this line, so does a reachability probe."""
    with socket.create_connection(("127.0.0.1", 2222), timeout=timeout) as s:
        s.settimeout(timeout)
        try:
            return s.recv(64)
        except TimeoutError:
            return b""


@pytest.fixture(scope="module")
def cluster():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(login_up, timeout=60), ssh_login("true").stderr
    assert wait_for(lambda: ssh_login("ssh", "cn001", "true").returncode == 0, timeout=30), ssh_login("ssh", "cn001", "true").stderr
    yield sim
    for undo in ({"login": "resume"}, {"login": "up"}, {"login": "accept_auth"}, {"login": "restore_host_key"}):
        scenario.execute(scenario.Step(0, "ssh", undo))
    stack.down(say=lambda m: None)


@pytest.fixture(autouse=True)
def healed(cluster):
    """Whatever a test breaks is undone before the next one, even when it fails halfway."""
    yield
    for undo in ({"login": "resume"}, {"login": "up"}, {"login": "accept_auth"}, {"login": "unlimit"},
                 {"login": "restore_host_key"}, {"nodes": ["cn001"], "sshd": "up"}):
        scenario.execute(scenario.Step(0, "ssh", undo))
    wait_for(login_up, timeout=30)


def test_hop_from_the_login_node_runs_on_the_node(cluster):
    r = ssh_login("ssh", "cn001", "hostname")
    assert r.returncode == 0 and r.stdout.strip() == "cn001", r.stderr
    gpu = ssh_login("ssh", "gpu001", "hostname")
    assert gpu.returncode == 0 and gpu.stdout.strip() == "gpu001", gpu.stderr


def test_hop_runs_slurm_tools_and_as_the_same_user(cluster):
    r = ssh_login("ssh", "cn001", "sinfo -h -o %N")  # non-interactive PATH on the node
    assert r.returncode == 0 and "cn[001-004]" in r.stdout, r.stderr
    assert ssh_login("ssh", "cn001", "id -un", user="bob").stdout.strip() == "bob"


def test_hop_is_checked_against_the_nodes_host_key(cluster):
    """No prompt on a first hop (the login node trusts the node key), and an unknown host is refused, not accepted."""
    assert ssh_login("ssh", "-o", "StrictHostKeyChecking=yes", "cn002", "true").returncode == 0
    assert ssh_login("ssh", "-o", "StrictHostKeyChecking=yes", "no-such-node", "true").returncode != 0


def test_proxyjump_from_the_client_through_the_login_node(cluster):
    proxy = (f"ssh -i {KEY} -p 2222 -o IdentitiesOnly=yes -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null "
             "-W %h:%p alice@127.0.0.1")
    r = subprocess.run(["ssh", "-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no",
                        "-o", "UserKnownHostsFile=/dev/null", "-o", "BatchMode=yes", "-o", f"ProxyCommand={proxy}",
                        "alice@cn001", "hostname"], capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "cn001", r.stderr


def test_srun_runs_on_the_node_with_and_without_a_pty(cluster):
    r = ssh_login("srun", "-N1", "-w", "cn001", "hostname")
    assert r.returncode == 0 and r.stdout.strip() == "cn001", r.stderr
    pty = ssh_login("srun", "-p", "gpu", "--pty", "-N1", "-w", "gpu001", "hostname", tty=True)
    assert pty.returncode == 0 and "gpu001" in pty.stdout, pty.stderr


def test_nodes_refuse_root_and_are_not_published(cluster):
    assert ssh_login("ssh", "root@cn001", "true").returncode != 0
    ports = subprocess.run(["docker", "port", "hpcsim-cn001-1"], capture_output=True, text=True, check=False).stdout
    assert ports.strip() == ""  # reachable only through the login node


def test_node_memory_stays_inside_its_limit(cluster):
    used = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.MemUsage}}", "hpcsim-cn001-1"],
                          capture_output=True, text=True, check=True).stdout.split("/")[0].strip()
    mib = float(used.rstrip("MiB")) if used.endswith("MiB") else 0.0
    assert 0 < mib < 55, used  # limit is 60 MB (slurmd + munge + sshd)


def test_hung_login_node_sends_no_banner_until_it_resumes(cluster):
    assert banner().startswith(b"SSH-")
    fault(login="hang")
    assert banner(timeout=2) == b""  # the port accepts, the banner never comes: a client's timeout path
    fault(login="resume")
    assert wait_for(lambda: banner().startswith(b"SSH-"), timeout=20)


def test_login_node_down_refuses_connections_then_returns(cluster):
    fault(login="down")
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 2222), timeout=3).close()
    fault(login="up")
    assert wait_for(login_up, timeout=60)


def test_rejected_auth_refuses_the_key_then_accepts_it_again(cluster):
    fault(login="reject_auth")
    assert wait_for(settled, timeout=15)
    r = ssh_login("true")
    assert r.returncode != 0 and "Permission denied" in r.stderr
    fault(login="accept_auth")
    assert wait_for(login_up, timeout=15)


def test_changed_host_key_is_refused_by_a_client_that_pinned_it(cluster, tmp_path):
    known = tmp_path / "known_hosts"
    scan = subprocess.run(["ssh-keyscan", "-p", "2222", "-t", "ed25519", "127.0.0.1"], capture_output=True, text=True, check=True)
    known.write_text(scan.stdout)
    assert ssh_login("true", known_hosts=known).returncode == 0
    fault(login="rotate_host_key")
    assert wait_for(lambda: banner().startswith(b"SSH-"), timeout=30)
    r = ssh_login("true", known_hosts=known)
    assert r.returncode != 0 and "REMOTE HOST IDENTIFICATION HAS CHANGED" in r.stderr
    fault(login="restore_host_key")
    assert wait_for(lambda: ssh_login("true", known_hosts=known).returncode == 0, timeout=30)  # the pin is valid again


def hold_one() -> socket.socket:
    """One unauthenticated connection that sshd has answered. Earlier probes may still count as pending for a moment
    (sshd reaps a closed connection a little later), so a refused attempt is closed and tried again."""
    held: list[socket.socket] = []

    def got_banner() -> bool:
        s = socket.create_connection(("127.0.0.1", 2222), timeout=3)
        s.settimeout(3)
        try:
            if s.recv(64).startswith(b"SSH-"):
                held.append(s)
                return True
        except OSError:
            pass
        s.close()
        return False

    assert wait_for(got_banner, timeout=20), "sshd never accepted a first connection under the limit"
    return held[0]


def test_startup_limit_refuses_an_extra_connection(cluster):
    fault(login="limit", max_startups=1)
    assert wait_for(settled, timeout=15)
    with hold_one():  # the one allowed unauthenticated connection
        r = ssh_login("true")
        assert r.returncode != 0 and "Connection" in r.stderr
    fault(login="unlimit")
    assert wait_for(login_up, timeout=15)


def test_a_node_whose_sshd_stopped_fails_the_hop_with_the_real_error(cluster):
    fault(nodes=["cn001"], sshd="down")
    r = ssh_login("ssh", "cn001", "true")
    assert r.returncode != 0 and "refused" in r.stderr.lower(), r.stderr
    assert ssh_login("ssh", "cn002", "true").returncode == 0  # the other nodes are untouched
    fault(nodes=["cn001"], sshd="up")
    assert wait_for(lambda: ssh_login("ssh", "cn001", "true").returncode == 0, timeout=15)
