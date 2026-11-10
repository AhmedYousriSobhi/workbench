"""Docker's own memory (containerd-shims, dockerd) in the measurement and the plan, and the per-node cost declaration (issue 456)."""
import json
from pathlib import Path

import yaml

from benchctl import compose, measure, memory, stack, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())
MIB = 1024**2


def fake_proc(tmp_path, procs):
    for pid, (comm, kb) in procs.items():
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "smaps_rollup").write_text(f"Pss: {kb} kB\n")
    return tmp_path


def test_docker_overhead_sums_shims_and_counts_them(tmp_path):
    p = fake_proc(tmp_path, {1: ("containerd-shim", 3072), 2: ("containerd-shim", 4096), 3: ("dockerd", 80 * 1024),
                             4: ("containerd", 40 * 1024), 5: ("bash", 9999)})
    got = measure.docker_overhead(p)
    assert got == {"shim": 7168 * 1024, "dockerd": 80 * MIB, "containerd": 40 * MIB, "shims": 2}


def test_the_summary_is_per_container_and_fixed_and_empty_without_shims():
    stats = {"overhead:shims": {"peak": 10}, "overhead:shim": {"peak": 35 * MIB}, "overhead:dockerd": {"peak": 80 * MIB},
             "overhead:containerd": {"peak": 40 * MIB}}
    assert measure.overhead_summary(stats) == {"shims": 10, "per_container_mb": 4, "fixed_mb": 120}  # 3.5 rounds up
    assert measure.overhead_summary({}) == {}


def test_the_report_shows_the_overhead_and_does_not_list_the_count_as_bytes():
    run = {"stats": {"docker:a": {"mean": 10 * MIB, "peak": 12 * MIB}, "overhead:shims": {"mean": 4, "peak": 4},
                     "overhead:shim": {"mean": 12 * MIB, "peak": 12 * MIB}, "overhead:dockerd": {"mean": 1, "peak": 70 * MIB},
                     "overhead:containerd": {"mean": 1, "peak": 30 * MIB}}}
    out = measure.report(run)
    assert "| docker:a |" in out and "overhead:shims" not in out
    assert "4 containerd-shims, 3 MB each" in out and "100 MB fixed" in out


def test_the_plan_adds_the_overhead_for_the_containers_a_start_creates(tmp_path):
    prof = compose.load_profile("std")
    base = workbench.estimate(SIM, prof)
    assert base["overhead_mb"] == 0 and "not measured" in base["overhead_note"]  # no figure on file: nothing invented
    memory.save_overhead(3, 120)
    est = workbench.estimate(SIM, prof)
    n = len(compose.generate(SIM, prof)["services"]) + len(workbench.OBSERVABILITY)
    assert est["overhead_mb"] == 3 * n and est["need_mb"] == base["need_mb"] + 3 * n and "3 MB per container" in est["overhead_note"]
    running = workbench.estimate(SIM, prof, running={"slurmctld"})  # a container that already runs has its shim already
    assert running["overhead_mb"] == 3 * (n - 1)


def test_the_plan_text_has_a_docker_overhead_line_with_its_source():
    spec = workbench.defaults(SIM)
    text = workbench.render(spec, workbench.estimate(*workbench.effective(SIM, spec)))
    assert "docker overhead" in text and "not measured" in text
    memory.save_overhead(3, 0)
    text = workbench.render(spec, workbench.estimate(*workbench.effective(SIM, spec)))
    assert "3 MB per container" in text


def test_a_saved_overhead_that_is_unreadable_counts_as_none(tmp_path):
    memory.OVERHEAD_FILE.write_text("not json")
    assert memory.saved_overhead() is None and memory.overhead_mb(5)[0] == 0
    memory.save_overhead(2, 50)
    assert json.loads(memory.OVERHEAD_FILE.read_text())["per_container_mb"] == 2 and memory.overhead_mb(5)[0] == 10


def test_missing_mb_includes_the_overhead_of_the_services_it_would_start(tmp_path):
    memory.save_overhead(5, 0)
    svcs = {"a": {"mem_limit": "100m"}, "b": {"mem_limit": "100m"}}

    class R:
        stdout = "a\n"
    assert stack.missing_mb(svcs, lambda cmd: R(), Path("x")) == memory.expected_mb("b", 100) + 5


def test_every_part_that_grows_with_the_node_count_has_a_declared_per_node_cost():
    """Generate two sizes and diff the services: whatever appears per added node must be in PER_NODE_MB, with its cap and its
    measurement under the declared cost. A new per-node part fails here until it declares what it costs."""
    small = compose.generate(SIM, {**compose.load_profile("std"), "compute_nodes": 2})["services"]
    big = compose.generate(SIM, {**compose.load_profile("std"), "compute_nodes": 5})["services"]
    grown = {n: big[n] for n in big.keys() - small.keys()}
    assert grown, "five nodes must add services"
    for name, svc in grown.items():
        key = memory.key_of(name, svc.get("labels"))
        assert key in memory.PER_NODE_MB, f"{name} is added per node: declare its cost in memory.PER_NODE_MB, or share it"
        cap = int(svc["mem_limit"].rstrip("m"))
        assert cap <= memory.PER_NODE_MB[key], f"{name}: cap {cap} MB is over its declared {memory.PER_NODE_MB[key]} MB per node"
    for key, declared in memory.PER_NODE_MB.items():
        assert memory.MEASURED_MB[key] <= declared, f"{key} measured {memory.MEASURED_MB[key]} MB, over its declared {declared} MB per node"
