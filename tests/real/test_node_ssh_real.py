"""sshd on the compute nodes: the login -> node hop, a client reaching a node through the login node, and srun.

This is what an SSH-based client does to open a shell on a compute node, and what a user does with `ssh cn001`.
"""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import bring_up, wait_for

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[2]
KEY = ROOT / stack.SSH_DIR / "id_ed25519"
OPTS = ["-i", str(KEY), "-o", "IdentitiesOnly=yes", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
        "-o", "BatchMode=yes", "-o", "LogLevel=ERROR", "-o", "ConnectTimeout=5"]


def login(*cmd: str, user: str = "alice", tty: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["ssh", *OPTS, *(["-tt"] if tty else []), "-p", str(compose.SSH_PORT), f"{user}@127.0.0.1", *cmd],
                          capture_output=True, text=True, timeout=60, check=False)


@pytest.fixture(scope="module")
def stack_up():
    os.chdir(ROOT)
    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    bring_up(sim)
    assert wait_for(lambda: login("ssh", "cn001", "true").returncode == 0, timeout=60), login("ssh", "cn001", "true").stderr
    yield sim
    stack.down(say=lambda m: None)


def test_user_hops_from_the_login_node_to_a_node_with_no_prompt(stack_up):
    r = login("ssh", "cn001", "hostname; id -un")
    assert r.returncode == 0 and r.stdout.split() == ["cn001", "alice"]


def test_slurm_tools_work_on_the_node(stack_up):
    r = login("ssh", "gpu001", "sinfo -h -o %N")
    assert r.returncode == 0 and "cn[001-004]" in r.stdout, r.stderr


def test_a_client_reaches_a_node_through_the_login_node(stack_up):
    """ProxyJump / forwardOut: the client's own key, a channel to cn002:22 opened by the login node."""
    jump = " ".join(["ssh", *OPTS, "-p", str(compose.SSH_PORT), "-W", "%h:%p", "alice@127.0.0.1"])
    r = subprocess.run(["ssh", *OPTS, "-o", f"ProxyCommand={jump}", "alice@cn002", "hostname"],
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and r.stdout.strip() == "cn002", r.stderr


def test_srun_runs_on_the_node_it_is_pinned_to(stack_up):
    r = login("srun", "-p", "gpu", "-N1", "-w", "gpu001", "hostname")
    assert r.returncode == 0 and r.stdout.strip() == "gpu001", r.stderr


def test_srun_with_a_pty_gives_an_interactive_shell_on_the_node(stack_up):
    r = login("srun", "--pty", "-N1", "-w", "cn003", "hostname", tty=True)
    assert r.returncode == 0 and "cn003" in r.stdout, r.stderr


def test_node_sshd_refuses_root_unknown_users_and_passwords(stack_up):
    assert login("ssh", "-o", "BatchMode=yes", "root@cn001", "true").returncode != 0
    assert login("ssh", "-o", "BatchMode=yes", "mallory@cn001", "true").returncode != 0
    r = login("ssh", "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password", "cn001", "true")
    assert r.returncode != 0


def test_nodes_share_one_host_key_the_login_node_trusts(stack_up):
    keys = {login("ssh-keyscan", "-t", "ed25519", n).stdout.split()[-1] for n in ("cn001", "cn004", "gpu002")}
    assert len(keys) == 1 and keys != {""}
