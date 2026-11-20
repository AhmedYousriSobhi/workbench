"""Apply a new architecture through the governor (#117): nodes are removed and added on a live stack."""
import os
import subprocess
from pathlib import Path

import pytest
import yaml
from helpers import ask, bring_up, start_governor, stop_governor, wait_for

from benchctl import stack, workbench

ROOT = Path(__file__).resolve().parents[2]


def services() -> set[str]:
    out = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.project=workbench", "--format",
                          '{{.Label "com.docker.compose.service"}}'], capture_output=True, text=True, check=False).stdout
    return set(out.split())


def slurm_nodes() -> set[str]:
    out = subprocess.run(["docker", "exec", "-u", "alice", "workbench-login-1", "sinfo", "-h", "-N", "-o", "%N"],
                         capture_output=True, text=True, check=False).stdout
    return set(out.split())


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    os.chdir(ROOT)
    tmp = tmp_path_factory.mktemp("gov")
    bring_up(yaml.safe_load((ROOT / "sim.yaml").read_text()))
    proc, _, sock = start_governor(tmp, "--user-mode", "--workdir", str(ROOT), env={"WORKBENCH_GOV_SOCKET": str(tmp / "gov.sock")})
    yield sock
    stop_governor(proc)
    stack.down(say=lambda m: None)
    workbench.forget()  # `benchctl up <selection>` saves its counts; leaving them would change every later test's node list


def test_apply_plan_shrinks_then_grows_the_running_workbench(cluster):
    plan = {"cmd": "apply-plan", "cpu_nodes": 2, "gpu_nodes": 1, "gpus_per_node": 8, "parts": ["jira", "bmc", "bcm"]}
    assert ask(cluster, plan)["started"]
    assert wait_for(lambda: {"cn001", "cn002", "gpu001"} <= services() and not ({"cn003", "cn004", "gpu002"} & services()), timeout=300)
    assert wait_for(lambda: slurm_nodes() == {"cn001", "cn002", "gpu001"}, timeout=120), slurm_nodes()
    # the governor writes `done` when the background benchctl command has exited, which can be a moment after the nodes change
    assert wait_for(lambda: any("apply-plan" in line and "done" in line for line in ask(cluster, {"cmd": "status"})["audit"]), timeout=120), \
        ask(cluster, {"cmd": "status"})["audit"][-8:]
    assert ask(cluster, {**plan, "cpu_nodes": 3, "gpu_nodes": 2})["started"]
    assert wait_for(lambda: {"cn003", "gpu002"} <= services(), timeout=300)
    # let the second `benchctl up` finish (it saves the spec last) before the fixture stops the governor mid-run
    assert wait_for(lambda: sum("apply-plan" in x and ": done" in x for x in ask(cluster, {"cmd": "status"})["audit"]) >= 2, timeout=300)


def test_applying_the_same_selection_twice_finishes_each_time_with_its_own_done_line(cluster):
    """Issue 202 and 212: the page waits for a `done` it has not seen, so two identical applies must not look alike. Each apply
    gets its own job id, `status.change` reports that job as done (what the page's applyOutcome reads), and the audit log holds
    one distinct `done` line per job."""
    plan = {"cmd": "apply-plan", "cpu_nodes": 3, "gpu_nodes": 2, "gpus_per_node": 8, "parts": ["jira", "bmc", "bcm"]}

    def done_lines() -> list[str]:
        return [x for x in ask(cluster, {"cmd": "status"})["audit"] if "apply-plan" in x and ": done" in x]

    def change() -> dict:
        return ask(cluster, {"cmd": "status"}).get("change") or {}

    def apply_and_wait(expect_done_lines: int) -> int:
        r = ask(cluster, plan)
        assert r["started"] and isinstance(r["job"], int), r
        assert wait_for(lambda: change().get("job") == r["job"] and change().get("state") == "done", timeout=300), change()
        assert wait_for(lambda: len(done_lines()) == expect_done_lines, timeout=60), done_lines()
        assert change()["finished"] and change()["finished"] >= change()["started"]
        return r["job"]

    before = len(done_lines())
    jobs = [apply_and_wait(before + 1), apply_and_wait(before + 2)]
    assert jobs[1] > jobs[0]
    new = done_lines()[before:]
    assert len(new) == 2 and new[0] != new[1], new  # the `(#N HH:MM:SS)` tag keeps identical changes apart
    assert {"cn001", "cn002", "cn003", "gpu001", "gpu002"} <= services()  # the same selection left the cluster as it was
