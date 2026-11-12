"""Real QOS, priority and preemption from sim.yaml (issue 544): validation, slurm.conf, the sacctmgr script, submit and the scenario."""
from pathlib import Path

import pytest
import yaml

from benchctl import compose, qos, scenario, slurmconf, workload

ROOT = Path(__file__).resolve().parents[1]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
QOS = [{"name": "scavenger", "priority": 1, "max_gpus_per_user": 2}, {"name": "urgent", "priority": 1000, "preempt": ["scavenger"], "grace_time_s": 30}]


def sim_with(**kw) -> dict:
    return {**SIM, **kw}


def test_nothing_changes_without_qos_or_priority():
    assert qos.problems(SIM, accounting=False) == [] and qos.slurm_lines(SIM) == []
    assert "Priority" not in slurmconf.slurm_conf(SIM, accounting=True)


def test_slurm_conf_gets_multifactor_priority_and_qos_preemption_only_with_accounting():
    sim = sim_with(qos=QOS, slurm={"priority": {"decay_half_life": "7-0", "weight_age": 100}})
    on = slurmconf.slurm_conf(sim, accounting=True).splitlines()
    for line in ("PriorityType=priority/multifactor", "PriorityDecayHalfLife=7-0", "PriorityWeightAge=100", "PriorityWeightQOS=1000", "AccountingStorageEnforce=limits,qos", "AccountingStorageTRES=gres/gpu",
                 "PreemptType=preempt/qos", "PreemptMode=REQUEUE"):
        assert line in on
    assert not [ln for ln in slurmconf.slurm_conf(sim, accounting=False).splitlines() if ln.startswith(("Priority", "Preempt"))]


def test_no_preempt_list_means_no_preempt_type():
    on = slurmconf.slurm_conf(sim_with(qos=[QOS[0]]), accounting=True)
    assert "PriorityType=priority/multifactor" in on and "PreemptType" not in on


def test_the_accounting_script_makes_the_qos_with_their_limits_and_lets_users_submit_under_them():
    sh = slurmconf.accounting_script(sim_with(qos=QOS)).splitlines()
    assert any(ln.startswith("for i in $(seq 30)") and "show tres" in ln for ln in sh[:8])  # the gpu TRES must exist before a limit names it
    assert "sacctmgr -i modify qos where name=scavenger set priority=1 MaxTRESPerUser=gres/gpu=2" in sh
    assert "sacctmgr -i modify qos where name=urgent set priority=1000 GraceTime=30 preempt=scavenger" in sh
    assert "sacctmgr -i add user alice account=sim qos=normal,scavenger,urgent defaultqos=normal || true" in sh
    assert "sacctmgr -i modify user where name=alice set qos=normal,scavenger,urgent defaultqos=normal" in sh
    assert sh.index("sacctmgr -i add qos urgent || true") < sh.index("sacctmgr -i add user alice account=sim qos=normal,scavenger,urgent defaultqos=normal || true")


def test_problems_name_each_mistake_once():
    cases = {
        "qos: [x]": ("qos must be a list of mappings", [{"name": "ok"}, "x"]),
        "bad name": ("lowercase", [{"name": "Bad"}]),
        "unknown key": ("unknown key 'nice'", [{"name": "a", "nice": 1}]),
        "number": ("priority must be a whole number", [{"name": "a", "priority": -1}]),
        "undeclared": ("not declared", [{"name": "a", "preempt": ["ghost"]}]),
        "self": ("cannot preempt itself", [{"name": "a", "preempt": ["a"]}]),
        "twice": ("declared twice", [{"name": "a"}, {"name": "a"}]),
    }
    for want, items in [v for v in cases.values()]:
        assert any(want in p for p in qos.problems(sim_with(qos=items))), want
    assert any("unknown key 'weight_x'" in p for p in qos.problems(sim_with(slurm={"priority": {"weight_x": 1}})))
    assert any("decay_half_life" in p for p in qos.problems(sim_with(slurm={"priority": {"decay_half_life": "soon"}})))
    ghost = sim_with(qos=QOS, users=[{"name": "carol", "qos": "gold"}])
    assert any("user carol" in p for p in qos.problems(ghost))


def test_one_message_when_accounting_is_off():
    sim = sim_with(qos=QOS)
    msg = qos.problems(sim, accounting=False)
    assert len(msg) == 1 and "need job accounting" in msg[0] and "--with accounting" in msg[0]
    assert qos.problems(sim, accounting=True) == []


def test_compose_refuses_qos_without_the_accounting_part():
    from benchctl import compose as c
    with pytest.raises(qos.QosError, match="need job accounting"):
        c.generate(sim_with(qos=QOS), {"groups": ["core"]})
    assert compose is c


def test_submit_can_name_a_qos():
    cmd = workload.command(sim_with(qos=QOS), "gpu001", 8, 600, "bob", qos="scavenger")
    assert "--qos=scavenger" in cmd[-1]
    assert "--qos" not in workload.command(sim_with(qos=QOS), "gpu001", 8, 600, "bob")[-1]
    with pytest.raises(workload.WorkloadError, match="unknown qos 'ghost'"):
        workload.command(SIM, "gpu001", 1, 60, "bob", qos="ghost")


def test_scenario_submit_passes_the_qos_and_preempt_burst_is_valid():
    sub = {"node": "gpu001", "user": "bob", "gpus": 8, "duration": 600, "qos": "scavenger"}
    assert scenario.daemon_problems({"submit": sub}) == []
    assert "--qos=scavenger" in scenario.submit_command(sub, sim_with(qos=QOS))[-1]
    assert scenario.validate(scenario.load("preempt-burst"), SIM) == []
