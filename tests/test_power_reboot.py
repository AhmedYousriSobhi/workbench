"""Slurm power save and reboot through the BMC farm (issue 545): the generated slurm.conf, the two scripts (run against stand-in scontrol and python3),
the scenario steps and the images that carry the scripts."""
import os
import subprocess
from pathlib import Path

import yaml

from benchctl import scenario, slurmconf

ROOT = Path(__file__).resolve().parents[1]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


class CP:
    def __init__(self, out="", code=0, err=""):
        self.stdout, self.returncode, self.stderr = out, code, err


def test_slurm_conf_names_the_power_and_reboot_programs_and_leaves_idle_suspend_off():
    text = slurmconf.slurm_conf(SIM)
    for line in ("SuspendProgram=/usr/local/sbin/power-off", "ResumeProgram=/usr/local/sbin/power-on", "RebootProgram=/usr/local/sbin/reboot.sh",
                 "ResumeTimeout=60"):
        assert line in text.splitlines()
    assert not [ln for ln in text.splitlines() if ln.startswith("SuspendTime=")]  # the default is off: nothing idles into power save by itself


def test_the_images_carry_the_scripts_executable():
    ctl = (ROOT / "images/slurm-ctl/Dockerfile").read_text()
    assert "COPY --chmod=755 power.sh /usr/local/sbin/power-off" in ctl and "COPY --chmod=755 power.sh /usr/local/sbin/power-on" in ctl
    assert "RUN apt" not in ctl  # ADR 0014: packages only in node-base's single layer
    assert "COPY --chmod=755 reboot.sh /usr/local/sbin/reboot.sh" in (ROOT / "images/node-base/Dockerfile").read_text()
    for f in ("images/slurm-ctl/power.sh", "images/node-base/reboot.sh"):
        assert os.access(ROOT / f, os.X_OK)


def run_power(tmp: Path, name: str, hosts: str, bmc_ok: bool = True) -> tuple[subprocess.CompletedProcess, list[str]]:
    """Run power.sh under `name` with a stand-in scontrol (`show hostnames LIST` expands the comma list) and python3 (called `-c CODE URL BODY`:
    records URL and BODY, and fails for the BMC when asked)."""
    bin_ = tmp / "bin"
    bin_.mkdir(parents=True)
    log = tmp / "posts.log"
    (bin_ / "scontrol").write_text('#!/bin/sh\nfor n in $(echo "$3" | tr "," " "); do echo $n; done\n')
    (bin_ / "python3").write_text(f'#!/bin/sh\necho "$3 $4" >> {log}\ncase "$3" in *bmc-farm*) exit {0 if bmc_ok else 1};; esac\nexit 0\n')
    script = tmp / name
    script.write_text((ROOT / "images/slurm-ctl/power.sh").read_text())
    for p in (bin_ / "scontrol", bin_ / "python3", script):
        p.chmod(0o755)
    r = subprocess.run(["sh", str(script), hosts], env={"PATH": f"{bin_}:/usr/bin:/bin"}, capture_output=True, text=True, check=False)
    return r, log.read_text().splitlines() if log.exists() else []


def test_power_off_resets_the_bmc_and_tells_the_bcm_and_the_exporter(tmp_path):
    r, posts = run_power(tmp_path, "power-off", "cn001,cn002")
    assert r.returncode == 0, r.stderr
    assert 'http://bmc-farm:8000/cn001/redfish/v1/Systems/1/Actions/ComputerSystem.Reset {"ResetType":"ForceOff"}' in posts
    assert 'http://bmc-farm:8000/cn002/redfish/v1/Systems/1/Actions/ComputerSystem.Reset {"ResetType":"ForceOff"}' in posts
    assert 'http://bcm-mock:8081/sim/inject {"node":"cn001","state":"DOWN"}' in posts
    assert 'http://gpu-exporter-farm:9400/sim/inject {"node":"cn002","down":true}' in posts


def test_power_on_is_the_other_way_round(tmp_path):
    r, posts = run_power(tmp_path, "power-on", "cn001")
    assert r.returncode == 0
    assert 'http://bmc-farm:8000/cn001/redfish/v1/Systems/1/Actions/ComputerSystem.Reset {"ResetType":"On"}' in posts
    assert 'http://bcm-mock:8081/sim/inject {"node":"cn001","state":"UP"}' in posts and 'down":false}' in posts[-1]


def test_a_bmc_that_refuses_fails_the_program_and_another_name_is_refused(tmp_path):
    r, _ = run_power(tmp_path, "power-off", "cn001", bmc_ok=False)
    assert r.returncode == 1 and "refused ResetType ForceOff" in r.stderr
    r2, _ = run_power(tmp_path / "x", "frobnicate", "cn001")
    assert r2.returncode == 2 and "power-off or power-on" in r2.stderr


def test_reboot_script_cycles_through_the_bmc_and_uses_the_hold_file():
    text = (ROOT / "images/node-base/reboot.sh").read_text()
    assert text.index("ForceOff") < text.index("touch \"$HOLD\"") < text.index('"ResetType":"On"') < text.index("rm -f \"$HOLD\"")
    assert "HOLD=/run/workbench/slurmd.hold" in text and "pkill -x slurmd" in text and "setsid" in text
    assert "HOLD=/run/workbench/slurmd.hold" in (ROOT / "images/node-base/slurmd-run").read_text()  # the same file the supervisor waits on


def test_scenario_steps_ask_slurmctld_for_power_and_reboot():
    cmds = scenario.power_commands({"power_down": ["cn001", "cn002"], "power_up": ["cn003"], "reboot": ["cn004"], "reason": "kernel update"})
    base = ["docker", "exec", scenario.SLURMCTLD, "scontrol"]
    assert cmds == [[*base, "power", "down", "cn001,cn002"], [*base, "power", "up", "cn003"],
                    [*base, "reboot", "ASAP", "nextstate=resume", "reason=kernel update", "cn004"]]
    assert scenario.power_commands({"drain": ["cn001"]}) == []
    run = []
    out = scenario.execute(scenario.Step(0, "slurm", {"reboot": ["cn002"]}), runner=lambda c: run.append(c) or CP())
    assert out.status == "ok" and run[0][4:7] == ["reboot", "ASAP", "nextstate=resume"]
    bad = scenario.execute(scenario.Step(0, "slurm", {"power_down": ["cn002"]}), runner=lambda c: CP("", 1, "Invalid node"))
    assert bad.status == "failed" and "Invalid node" in bad.detail


def test_reboot_asap_scenario_is_valid_and_needs_the_host_cli():
    sc = scenario.load("reboot-asap")
    assert scenario.validate(sc, SIM) == []
    assert scenario.validate({"steps": [{"at": "0s", "target": "slurm", "do": {"reboot": ["cn999"]}}]}, SIM)  # unknown node
    step = scenario.Step(0, "slurm", {"power_down": ["cn001"]})
    assert "host CLI" in scenario.page_support(step)
    assert scenario.execute(step, host=False, slurm=lambda *a: (True, "")).status == "skipped"  # not silently done by the governor's state steps
