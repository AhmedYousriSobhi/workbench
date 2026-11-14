"""In-place Slurm upgrade steps (issue 221, ADR 0079): order, commands, refusals."""
import subprocess
from pathlib import Path

import yaml

from benchctl import scenario, slurmupgrade

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


class Fake:
    """Records commands; every container reports the version its tree holds."""
    def __init__(self, missing_image=False, fail_on=None):
        self.cmds, self.ver, self.missing, self.fail_on = [], {}, missing_image, fail_on

    def __call__(self, cmd, timeout=10):
        self.cmds.append(cmd)
        out, rc = "", 0
        if cmd[:3] == ["docker", "image", "inspect"] and self.missing:
            rc = 1
        if cmd[:2] == ["docker", "create"]:
            out = "tmp123\n"
        if cmd[-2:] == ["/opt/slurm/bin/sinfo", "-V"] or cmd[-1] == "-V":
            out = f"slurm {self.ver.get(cmd[2], '25.11.9')}\n"
        if cmd[:2] == ["docker", "cp"]:
            self.ver[cmd[3].split(":")[0]] = "26.05.5"
        if self.fail_on and self.fail_on in cmd:
            rc = 1
        return subprocess.CompletedProcess(cmd, rc, out, "boom" if rc else "")


def test_the_order_is_schedmds_and_login_is_last():
    assert slurmupgrade.ordered(["login", "cn001", "slurmrestd", "slurmctld", "slurmdbd"]) == ["slurmdbd", "slurmctld", "slurmrestd", "cn001", "login"]


def test_a_step_names_a_listed_release_and_known_daemons():
    nodes = {"cn001"}
    assert slurmupgrade.problems({"to": "26.05.5", "daemons": ["slurmdbd", "cn001", "login"]}, nodes) == []
    assert "upgrade.to is 1.2.3" in slurmupgrade.problems({"to": "1.2.3", "daemons": ["cn001"]}, nodes)[0]
    assert "unknown daemon 'cn999'" in slurmupgrade.problems({"to": "26.05.5", "daemons": ["cn999"]}, nodes)[0]
    assert "needs daemons" in slurmupgrade.problems({"to": "26.05.5"}, nodes)[0]


def test_the_scenario_validates_and_counts_as_disruptive():
    sc = scenario.load("slurm-upgrade-mid-job")
    assert scenario.validate(sc, SIM) == [] and scenario.disruptive(sc)


def test_each_daemon_gets_the_tree_copied_beside_the_old_one_then_renamed_and_restarted_alone():
    f = Fake()
    msgs = []
    assert slurmupgrade.apply({"to": "26.05.5", "daemons": ["cn001", "slurmdbd"]}, f, say=msgs.append, sleep=lambda s: None) is None
    flat = [" ".join(c) for c in f.cmds]
    assert flat.index("docker restart -t 10 workbench-slurmdbd-1") < flat.index("docker exec workbench-cn001-1 pkill -x slurmd")
    assert "docker cp tmp123:/opt/slurm/. workbench-cn001-1:/opt/slurm.next" in flat
    assert not any(c[:2] in (["docker", "run"], ["docker", "up"]) or "rm" in c[:2] and c[2] != "-f" for c in f.cmds)  # nothing recreated
    assert flat[-1] == "docker rm -f tmp123" and msgs == ["upgraded slurmdbd: 25.11.9 -> 26.05.5", "upgraded cn001: 25.11.9 -> 26.05.5"]


def test_login_gets_the_tree_but_nothing_is_restarted():
    f = Fake()
    assert slurmupgrade.apply({"to": "26.05.5", "daemons": ["login"]}, f, say=lambda m: None, sleep=lambda s: None) is None
    assert not any(c[:2] == ["docker", "restart"] or "pkill" in c for c in f.cmds)


def test_a_missing_image_is_built_with_the_checked_release_and_checksum():
    f = Fake(missing_image=True)
    slurmupgrade.apply({"to": "25.11.9", "daemons": ["login"]}, f, say=lambda m: None, sleep=lambda s: None)
    build = next(c for c in f.cmds if c[:2] == ["docker", "build"])
    assert "workbench/node-base:dev-25.11.9" in build and "SLURM_VERSION=25.11.9" in build and any(a.startswith("SLURM_SHA256=d238") for a in build)


def test_a_failed_copy_stops_with_the_daemon_named_and_still_removes_the_temporary_container():
    f = Fake(fail_on="cp")
    bad = slurmupgrade.apply({"to": "26.05.5", "daemons": ["cn001"]}, f, say=lambda m: None, sleep=lambda s: None)
    assert bad and bad.startswith("cn001:") and f.cmds[-1][:3] == ["docker", "rm", "-f"]
