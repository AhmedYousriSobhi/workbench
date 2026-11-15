"""Config lab downscale, name mapping, time scaling and sampling (issue 551). Synthetic data only."""
import json
import shutil
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from benchctl import cli, lab, lab_replay, lab_slurm
from benchctl import lab_import as li

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures" / "lab" / "full"
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def node(name, shape="a", parts=("p",), leaf="l1"):
    return {"name": name, "shape": shape, "partitions": list(parts), "leaf": leaf}


def big_cluster():
    """1000 CPU nodes in 2 leaves in partition cpu, 200 GPU nodes in partition gpu (1 leaf), 8 big-memory nodes in cpu+bigmem."""
    return ([node(f"c{i:04d}", "cpu", ("cpu",), "leaf1" if i % 2 else "leaf2") for i in range(1000)]
            + [node(f"g{i:03d}", "gpu", ("gpu",), "leaf3") for i in range(200)]
            + [node(f"m{i}", "mem", ("bigmem", "cpu"), "leaf1") for i in range(8)])


# ---- key classes
@pytest.mark.parametrize("key,cls", [
    ("PriorityWeightQOS", "policy"), ("priorityweightqos", "policy"), ("SchedulerParameters", "policy"),
    ("PriorityDecayHalfLife", "time-scaled"), ("MinJobAge", "time-scaled"), ("PreemptType", "restart"), ("GresTypes", "restart"),
    ("StoragePass", "lab-owned"), ("SlurmctldHost", "lab-owned"), ("Prolog", "lab-owned"), ("SomethingNew", "unknown")])
def test_key_classes(key, cls):
    assert lab_slurm.classify(key) == cls


def test_restart_rule_is_restart_class_type_suffix_or_unknown():
    assert lab_slurm.restart_required("PreemptType") and lab_slurm.restart_required("GresTypes")
    assert lab_slurm.restart_required("BrandNewKey") and lab_slurm.restart_required("FutureType")
    assert not lab_slurm.restart_required("PriorityWeightAge") and not lab_slurm.restart_required("MinJobAge")


def test_key_classes_do_not_overlap_and_cover_the_workbench_generated_keys():
    seen = {}
    for cls, names in (("policy", lab_slurm._POLICY), ("restart", lab_slurm._RESTART), ("lab-owned", lab_slurm._LAB_OWNED), ("time-scaled", lab_slurm._TIME_SCALED)):
        for k in names:
            assert seen.setdefault(k, cls) == cls, k
    group = lab_slurm.classify_keys({"A": "1", "PriorityWeightAge": "1", "StoragePass": "x"})
    assert group["unknown"] == ["A"] and group["policy"] == ["PriorityWeightAge"] and group["lab-owned"] == ["StoragePass"]


# ---- durations and time scaling
@pytest.mark.parametrize("text,bare,sec", [("90", "min", 5400), ("90", "s", 90), ("5:30", "min", 330), ("1:02:03", "min", 3723),
                                           ("7-0", "min", 604800), ("1-12:30", "min", 131400), ("2-00:00:00", "min", 172800)])
def test_parse_duration(text, bare, sec):
    assert lab_slurm.parse_duration(text, bare) == sec


def test_parse_duration_unlimited_and_garbage():
    assert lab_slurm.parse_duration("UNLIMITED") is None and lab_slurm.parse_duration("INFINITE") is None
    for bad in ("x", "1:2:3:4", "a-1"):
        with pytest.raises(lab_slurm.LabError):
            lab_slurm.parse_duration(bad)


def test_format_and_scale():
    assert lab_slurm.format_duration(7200) == "02:00:00" and lab_slurm.format_duration(90000) == "1-01:00:00"
    assert lab_slurm.scale_seconds(100, 1000) == 1  # never below a second
    assert lab_slurm.scale_time_value("7-0", "time", 84) == "02:00:00"
    assert lab_slurm.scale_time_value("300", "s", 10) == "30" and lab_slurm.scale_time_value("5", "min", 100) == "1"
    assert lab_slurm.scale_time_value("UNLIMITED", "s", 10) == "UNLIMITED"
    assert lab_slurm.scale_scheduler_parameters("bf_interval=60,bf_window=2880,bf_continue,default_queue_depth=100", 10) == \
        "bf_interval=6,bf_window=288,bf_continue,default_queue_depth=100"


def test_dilate_keys_scales_only_time_based_parameters_and_lists_them():
    keys = {"PriorityDecayHalfLife": "7-0", "PriorityWeightAge": "1000", "MinJobAge": "300", "SchedulerParameters": "bf_interval=30",
            "SlurmdTimeout": "300", "PriorityMaxAge": "UNLIMITED"}
    out, changed = lab_slurm.dilate_keys(keys, 60)
    assert out["PriorityDecayHalfLife"] == "02:48:00" and out["MinJobAge"] == "5" and out["SchedulerParameters"] == "bf_interval=1"
    assert out["PriorityWeightAge"] == "1000" and out["SlurmdTimeout"] == "300" and out["PriorityMaxAge"] == "UNLIMITED"
    assert [c["key"] for c in changed] == ["PriorityDecayHalfLife", "MinJobAge", "SchedulerParameters"]
    assert lab_slurm.dilate_keys(keys, 1.0)[1] == [] or all(c["from"] != c["to"] for c in lab_slurm.dilate_keys(keys, 1.0)[1])


def test_partition_and_qos_limits_use_the_same_factor():
    assert lab_slurm.dilate_partition({"PartitionName": "p", "MaxTime": "2-00:00:00", "DefaultTime": "UNLIMITED"}, 48)["MaxTime"] == "01:00:00"
    assert lab_slurm.dilate_partition({"PartitionName": "p", "DefaultTime": "UNLIMITED"}, 48)["DefaultTime"] == "UNLIMITED"
    assert lab_slurm.dilate_qos({"Name": "high", "GraceTime": "00:10:00", "MaxWall": "1-00:00:00"}, 10) == \
        {"Name": "high", "GraceTime": "00:01:00", "MaxWall": "02:24:00"}


# ---- downscale
def test_downscale_keeps_every_group_and_splits_the_rest_in_proportion():
    nodes = big_cluster()
    plan = lab_slurm.downscale(nodes, 24)
    assert plan["production_nodes"] == 1208 and plan["lab_nodes"] == 24
    by = {(tuple(g["partitions"]), g["shape"], g["leaf"]): g for g in plan["groups"]}
    assert len(by) == 4 and all(g["lab"] >= 1 for g in by.values())
    assert by[(("bigmem", "cpu"), "mem", "leaf1")]["lab"] == 1  # 8 nodes keep one
    assert by[(("cpu",), "cpu", "leaf1")]["lab"] == by[(("cpu",), "cpu", "leaf2")]["lab"]
    assert sum(g["lab"] for g in by.values()) == 24
    assert by[(("cpu",), "cpu", "leaf1")]["lab"] > by[(("gpu",), "gpu", "leaf3")]["lab"] > 1  # 500 vs 200 production nodes


def test_downscale_keeps_production_names_in_production_order_and_maps_services():
    plan = lab_slurm.downscale(big_cluster(), 24)
    assert plan["nodes"] == sorted(plan["nodes"], key=[n["name"] for n in big_cluster()].index)
    assert plan["nodes"][0] == "c0000" and plan["services"]["c0000"] == "lab-node-001"
    assert list(plan["services"].values())[-1] == "lab-node-024"


def test_downscale_never_exceeds_the_production_count_or_a_groups_size():
    plan = lab_slurm.downscale([node("a"), node("b"), node("c", "g")], 24)
    assert plan["lab_nodes"] == 3 and plan["ratio"] == 1.0 and not plan["indicative"]
    plan = lab_slurm.downscale([node(f"a{i}") for i in range(10)] + [node("x", "g")], 6)
    assert [g["lab"] for g in plan["groups"]] == [5, 1]


def test_downscale_refuses_when_groups_do_not_fit_one_message_with_a_hint():
    nodes = [node(f"n{i}", shape=str(i)) for i in range(30)]
    with pytest.raises(lab_slurm.LabError, match="30 groups; raise lab.max_nodes to 30"):
        lab_slurm.downscale(nodes, 24)
    many = [node(f"n{i}", shape=str(i)) for i in range(70)]
    with pytest.raises(lab_slurm.LabError, match="more than the 64 nodes"):
        lab_slurm.downscale(many, 64)
    for bad in (0, 65):
        with pytest.raises(lab_slurm.LabError, match="from 1 to 64"):
            lab_slurm.downscale(nodes, bad)


def test_ratio_above_100x_marks_results_indicative():
    nodes = [node(f"n{i:04d}") for i in range(2500)]
    assert lab_slurm.downscale(nodes, 24)["indicative"] is True      # 104x
    assert lab_slurm.downscale(nodes[:2400], 24)["indicative"] is False  # exactly 100x


def test_production_nodes_from_the_parsed_files():
    slurm = li.parse_slurm_conf((FIX / "slurm.conf").read_text())
    nodes = lab_slurm.production_nodes(slurm, li.parse_topology((FIX / "topology.conf").read_text()), li.parse_gres((FIX / "gres.conf").read_text()))
    assert [n["name"] for n in nodes] == ["cpu001", "cpu002", "cpu003", "cpu004", "gpu001", "gpu002"]
    assert nodes[0]["partitions"] == ["cpu"] and nodes[4]["partitions"] == ["gpu"]
    assert nodes[0]["leaf"] == "leaf1" and nodes[3]["leaf"] == "leaf2" and nodes[4]["leaf"] == "leaf1"
    assert nodes[0]["shape"] == nodes[3]["shape"] != nodes[4]["shape"]


def test_production_nodes_resolve_nodesets_all_and_reject_unknown_nodes():
    conf = li.parse_slurm_conf("NodeName=n[1-3] CPUs=1\nNodeSet=front Nodes=n1\nPartitionName=a Nodes=front,n3\nPartitionName=b Nodes=ALL\n")
    nodes = lab_slurm.production_nodes(conf, [], [])
    assert [n["partitions"] for n in nodes] == [["a", "b"], ["b"], ["a", "b"]]
    bad = li.parse_slurm_conf("NodeName=n1 CPUs=1\nPartitionName=a Nodes=n9\n")
    with pytest.raises(lab_slurm.LabError, match="n9"):
        lab_slurm.production_nodes(bad, [], [])
    with pytest.raises(lab_slurm.LabError, match="no NodeName"):
        lab_slurm.production_nodes(li.parse_slurm_conf("A=1\n"), [], [])


def test_admission_estimate_counts_every_lab_node():
    assert lab_slurm.admission_mb(24) == 24 * 13 + 250 and lab_slurm.admission_mb(64) == 64 * 13 + 250


def test_lab_settings_defaults_and_checks():
    assert lab_slurm.settings({}) == {"max_nodes": 24, "max_running_jobs": 50, "target_hours": 2.0}
    assert lab_slurm.problems({"lab": {"max_nodes": 30}}) == []
    msgs = lab_slurm.problems({"lab": {"max_nodes": 65, "max_running_jobs": 0, "target_hours": 9}})
    assert len(msgs) == 3
    assert lab_slurm.problems({"lab": {"max_nodes": True}})


# ---- trace: sampling, users, time, concurrency
def jobs(n_a=60, n_b=20, n_c=3):
    out = []
    for i in range(n_a):
        out.append({"JobID": str(i), "User": f"u{i % 7}", "Account": "A", "QOS": "normal", "Partition": "cpu", "Submit": "2026-01-05T08:00:00",
                    "Start": f"2026-01-05T08:{i % 60:02d}:00", "End": f"2026-01-05T09:{i % 60:02d}:00", "Elapsed": "01:00:00", "Timelimit": "02:00:00"})
    for i in range(n_b):
        out.append({"JobID": f"b{i}", "User": "zed", "Account": "B", "QOS": "high", "Partition": "gpu", "Submit": "2026-01-05T08:10:00",
                    "Start": "2026-01-05T08:30:00", "End": "2026-01-05T10:30:00", "Elapsed": "02:00:00", "Timelimit": "UNLIMITED"})
    for i in range(n_c):
        out.append({"JobID": f"c{i}", "User": "amy", "Account": "C", "QOS": "normal", "Partition": "cpu", "Submit": "2026-01-05T08:20:00",
                    "Start": "Unknown", "End": "Unknown", "Elapsed": "00:00:00", "Timelimit": "01:00:00"})
    return out


def test_sampling_is_stratified_deterministic_and_keeps_every_stratum():
    js = jobs()
    s = lab_replay.sample(js, 0.1)
    by = lambda rows, a: sum(1 for r in rows if r["Account"] == a)
    assert (by(s, "A"), by(s, "B"), by(s, "C")) == (6, 2, 1)  # 60 and 20 at 10 percent; the 3-job stratum keeps one
    assert s == lab_replay.sample(js, 0.1) and s != lab_replay.sample(js, 0.1, seed=2)
    assert [j["JobID"] for j in s] == sorted((j["JobID"] for j in s), key=[x["JobID"] for x in js].index)  # original order
    assert lab_replay.sample(js, 1) == js
    for bad in (0, 1.5, -1):
        with pytest.raises(lab_replay.LabError):
            lab_replay.sample(js, bad)


def test_user_mapping_is_stable_and_can_be_kept():
    js = jobs()
    m = lab_replay.map_users(js)
    assert m["amy"] == "lab-u001" and m["u0"] == "lab-u002" and m["zed"] == "lab-u009" and len(set(m.values())) == 9
    assert lab_replay.map_users(js, keep=True) == {u: u for u in m}
    mapped = lab_replay.apply_users(js[:2], m)
    assert [j["User"] for j in mapped] == ["lab-u002", "lab-u003"] and js[0]["User"] == "u0"  # input untouched
    big = [{"User": f"x{i:04d}"} for i in range(1000)]
    assert lab_replay.map_users(big)["x0000"] == "lab-u0001"


def test_time_span_factor_and_clamps():
    js = jobs()
    assert lab_replay.span_s(js) == 2 * 3600 + 30 * 60  # 08:00 submit to the 10:30 end
    assert lab_replay.time_factor(js, 2) == 1.25
    assert lab_replay.time_factor(js[:1], 2) == 1.0  # a short trace is not stretched
    week = [{"Submit": "2026-01-05T08:00:00", "Start": "2026-01-05T08:00:00", "End": "2026-01-12T08:00:00", "Elapsed": "7-00:00:00"}]
    assert lab_replay.time_factor(week, 2) == 84.0
    for bad in (0.1, 7):
        with pytest.raises(lab_replay.LabError, match="0.5 to 6"):
            lab_replay.time_factor(js, bad)
    with pytest.raises(lab_replay.LabError, match="no job with a Submit"):
        lab_replay.span_s([{"Submit": "Unknown"}])
    with pytest.raises(lab_replay.LabError, match="bad time"):
        lab_replay.parse_time("yesterday")


def test_scale_job_divides_offsets_runs_and_limits_by_one_factor():
    t0 = lab_replay.parse_time("2026-01-05T08:00:00")
    j = {"Submit": "2026-01-05T09:00:00", "Elapsed": "01:00:00", "Timelimit": "02:00:00"}
    assert lab_replay.scale_job(j, t0, 30) == {"submit_s": 120.0, "run_s": 120, "limit_s": 240}
    assert lab_replay.scale_job({**j, "Timelimit": "UNLIMITED"}, t0, 30)["limit_s"] is None
    assert lab_replay.scale_job({**j, "Elapsed": "00:00:00"}, t0, 30)["run_s"] == 0


def test_peak_concurrency_ignores_jobs_that_never_ran_and_frees_a_slot_at_the_same_instant():
    a = {"Start": "2026-01-05T08:00:00", "End": "2026-01-05T09:00:00"}
    b = {"Start": "2026-01-05T09:00:00", "End": "2026-01-05T10:00:00"}
    c = {"Start": "Unknown", "End": "Unknown"}
    assert lab_replay.peak_concurrency([a, b, c]) == 1
    assert lab_replay.peak_concurrency([a, a, b, c]) == 2
    assert lab_replay.peak_concurrency([{"Start": "2026-01-05T08:00:00", "End": "Unknown", "Elapsed": "00:30:00"}]) == 1
    assert lab_replay.peak_concurrency([]) == 0


def test_concurrency_proposal_shrinks_the_nodes_until_the_sampled_peak_fits():
    nodes = [node(f"n{i:03d}", "a", ("p",), "l1") for i in range(400)] + [node(f"g{i}", "g", ("q",), "l2") for i in range(100)]
    js = [{"JobID": str(i), "User": "u", "Account": "A", "QOS": "n", "Partition": "p", "Submit": "2026-01-05T08:00:00",
           "Start": "2026-01-05T08:00:00", "End": "2026-01-05T09:00:00", "Elapsed": "01:00:00"} for i in range(1000)]
    ok = lab_replay.plan_load(nodes, js, 24, 1000)
    assert ok["fits"] and ok["proposed_nodes"] == ok["requested_nodes"] == 24 and ok["peak"] <= 1000
    small = lab_replay.plan_load(nodes, js, 24, 12)
    assert small["fits"] and small["peak"] <= 12 and small["proposed_nodes"] < 24 and small["requested_nodes"] == 24
    floor = lab_replay.plan_load(nodes, js, 24, 1)  # two groups: cannot go below 2 nodes, and 2 nodes sample 8 jobs
    assert not floor["fits"] and floor["proposed_nodes"] == 2


# ---- the plan and the CLI
def import_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "ROOT", tmp_path / "lab")
    d = tmp_path / "export"
    shutil.copytree(FIX, d)
    lab.import_lab("demo", d)


def test_plan_over_an_imported_lab(tmp_path, monkeypatch):
    import_fixture(tmp_path, monkeypatch)
    p = lab.plan("demo", SIM)
    assert p["scale"]["production_nodes"] == 6 and p["scale"]["lab_nodes"] == 6 and not p["indicative"]
    assert p["jobs"]["production"] == 3 and p["jobs"]["users"] == 2 and p["jobs"]["user_map"] == {"u1": "lab-u001", "u2": "lab-u002"}
    assert p["time"]["factor"] == 1.5 and p["key_classes"]["lab-owned"] and p["admission_mb"] == 6 * 13 + 250
    kept = lab.plan("demo", SIM, keep_users=True)["jobs"]["user_map"]
    assert kept == {"u1": "u1", "u2": "u2"}
    text = lab.plan_text(p)
    assert "6 production nodes -> 6 lab nodes" in text and "memory the governor must find free" in text
    with pytest.raises(lab.LabError, match="4 groups; raise lab.max_nodes to 4"):  # two shapes on two leaves
        lab.plan("demo", SIM, max_nodes=3)


def test_plan_without_a_trace_or_slurm_conf(tmp_path, monkeypatch):
    monkeypatch.setattr(lab, "ROOT", tmp_path / "lab")
    d = tmp_path / "export"
    shutil.copytree(FIX, d)
    (d / "trace.sacct").unlink()
    lab.import_lab("notrace", d)
    p = lab.plan("notrace", SIM)
    assert p["jobs"] is None and "no replay possible" in lab.plan_text(p)
    (tmp_path / "e2").mkdir()
    (tmp_path / "e2" / "qos.txt").write_text((FIX / "qos.txt").read_text())
    lab.import_lab("onlyqos", tmp_path / "e2")
    with pytest.raises(lab.LabError, match="no slurm.conf"):
        lab.plan("onlyqos", SIM)
    with pytest.raises(lab.LabError, match="no lab ghost"):
        lab.plan("ghost", SIM)


def test_cli_get_lab(tmp_path, monkeypatch):
    import_fixture(tmp_path, monkeypatch)
    monkeypatch.chdir(ROOT)
    r = CliRunner().invoke(cli.app, ["get", "lab", "demo"])
    assert r.exit_code == 0 and "6 production nodes -> 6 lab nodes" in r.output
    r = CliRunner().invoke(cli.app, ["get", "lab", "demo", "-o", "json", "--max-nodes", "4", "--target-hours", "1"])
    assert r.exit_code == 0 and json.loads(r.output)["settings"] == {"max_nodes": 4, "max_running_jobs": 50, "target_hours": 1.0}
    r = CliRunner().invoke(cli.app, ["get", "lab", "nope"])
    assert r.exit_code == 1 and "no lab nope" in r.output
    assert CliRunner().invoke(cli.app, ["get", "lab", "demo", "--max-nodes", "99"]).exit_code == 2
