import json
import subprocess
import tarfile

import pytest

from simctl import checks, diag

GB = checks.GB
PROFILES = {"min": 3.0, "std": 6.0, "full": 9.0}


def test_preflight_allows_and_refuses():
    ok, msg = checks.preflight("std", PROFILES, 10 * GB)
    assert ok and "remain" in msg
    ok, msg = checks.preflight("full", PROFILES, 9 * GB)
    assert not ok and "try --profile" in msg
    ok, msg = checks.preflight("std", PROFILES, 7 * GB)  # leaves 1 GB < 1.5 GB floor
    assert not ok and "--profile min" in msg
    ok, msg = checks.preflight("min", PROFILES, 2 * GB)
    assert not ok and "no profile fits" in msg
    assert not checks.preflight("nope", PROFILES, 99 * GB)[0]


def test_kernel_check():
    assert checks.check_kernel("6.8.0-1").status == checks.OK
    assert checks.check_kernel("5.4.0").status == checks.WARN


def test_swap_check():
    full = "SwapTotal: 2097152 kB\nSwapFree: 1000 kB\n"
    assert checks.check_swap(full, False).status == checks.WARN
    assert checks.check_swap("SwapTotal: 0 kB\nSwapFree: 0 kB\n", False).status == checks.WARN
    assert checks.check_swap("SwapTotal: 100 kB\nSwapFree: 90 kB\n", True).status == checks.OK


def test_docker_check_variants():
    def r(out, rc=0):
        return lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, rc, out, "boom")

    assert checks.check_docker(r("systemd 2")).status == checks.OK
    assert checks.check_docker(r("cgroupfs 2")).status == checks.FAIL
    assert checks.check_docker(r("", 1)).status == checks.FAIL


def test_governor_unreachable_is_warn(tmp_path):
    assert checks.check_governor(str(tmp_path / "none.sock")).status == checks.WARN


def test_tools_marks_missing():
    res = {c.name: c.status for c in checks.check_tools(lambda t: None if t == "k3d" else "/x")}
    assert res["tool k3d"] == checks.WARN and res["tool docker"] == checks.OK
    assert {c.status for c in checks.check_tools(lambda t: None)} == {checks.FAIL, checks.WARN}


def test_load_profiles_reads_repo():
    p = checks.load_profiles()
    assert {"min", "std", "full"} <= set(p)


def test_mask_secrets():
    t = ("origin https://" + "ghp_" + "A" * 30 + "@github.com/x\n"
         'password: hunter2\ntoken="abc123"\nok line\n'
         "url https://user:pw@host/path\n")
    m = diag.mask(t)
    assert "hunter2" not in m and "abc123" not in m and "ghp_" not in m and "user:pw" not in m
    assert "ok line" in m


def test_bundle_has_no_tokens(tmp_path):
    out = diag.write_bundle(tmp_path / "d.tar.gz")
    with tarfile.open(out) as tar:
        names = tar.getnames()
        blob = b"".join(tar.extractfile(n).read() for n in names if tar.getmember(n).isfile())
    assert "diag/doctor.txt" in names and "diag/proc_meminfo.txt" in names
    assert b"ghp_" not in blob and b"github_pat_" not in blob


# --- Phase 0 measurement -------------------------------------------------------------------

def test_parse_size_and_docker_stats():
    from simctl import measure

    assert measure.parse_size("12.5MiB") == int(12.5 * 1024**2)
    assert measure.parse_size("1GiB") == 1024**3
    with pytest.raises(ValueError):
        measure.parse_size("lots")
    out = "prometheus\t180MiB / 300MiB\ngrafana\t90.5MiB / 300MiB\n"
    assert measure.parse_docker_stats(out) == {"prometheus": 180 * 1024**2, "grafana": int(90.5 * 1024**2)}


def test_parse_dommemstat_prefers_rss():
    from simctl import measure

    assert measure.parse_dommemstat("actual 2097152\nrss 1048576\n") == 1048576 * 1024
    assert measure.parse_dommemstat("actual 2097152\n") == 2097152 * 1024
    assert measure.parse_dommemstat("") == 0


def test_pss_falls_back_to_rss(tmp_path):
    from simctl import measure

    (tmp_path / "status").write_text("Name:\tx\nVmRSS:\t  500 kB\n")
    assert measure.pss_kb(tmp_path) == 500
    (tmp_path / "smaps_rollup").write_text("Rss: 900 kB\nPss: 400 kB\n")
    assert measure.pss_kb(tmp_path) == 400
    assert measure.pss_kb(tmp_path / "nope") is None


def test_processes_sum_by_name(tmp_path):
    from simctl import measure

    for pid, comm, kb in ((1, "python", 100), (2, "python", 50), (3, "redis", 10)):
        d = tmp_path / str(pid)
        d.mkdir()
        (d / "comm").write_text(comm + "\n")
        (d / "smaps_rollup").write_text(f"Pss: {kb} kB\n")
    (tmp_path / "meminfo").write_text("x")  # non-pid entries are ignored
    assert measure.processes(tmp_path) == {"python": 150 * 1024, "redis": 10 * 1024}


def test_aggregate_sample_and_save(tmp_path):
    from simctl import measure

    seq = iter([{"docker:a": 100, "vm:x": 50}, {"docker:a": 300}, {"docker:a": 200}])
    snaps = measure.sample(10, 5, take=lambda: next(seq), sleep=lambda s: None)
    assert len(snaps) == 3
    agg = measure.aggregate(snaps)
    assert agg["docker:a"] == {"mean": 200, "peak": 300}
    assert agg["vm:x"] == {"mean": 16, "peak": 50}  # absent samples count as zero
    path = measure.save("t", snaps, tmp_path)
    assert json.loads(path.read_text())["stats"]["docker:a"]["peak"] == 300


def test_report_suggests_headroom():
    from simctl import measure

    mib = 1024**2
    idle = {"stats": {"docker:a": {"mean": 100 * mib, "peak": 100 * mib}, "host:used": {"mean": 1, "peak": 1}}}
    loaded = {"stats": {"docker:a": {"mean": 200 * mib, "peak": 250 * mib}}}
    rep = measure.report(idle, loaded)
    assert "| docker:a | 100 | 250 | 300 |" in rep
    assert "host:" not in rep


def test_snapshot_is_flat_and_readonly_on_this_host():
    from simctl import measure

    s = measure.snapshot()
    assert s["host:available"] > 0
    assert all(isinstance(v, int) for v in s.values())


# --- container security monitoring --------------------------------------------------------

def _ctr(name="/cn001", **hc):
    return {"Name": name, "HostConfig": {"Memory": 1, **hc}, "Mounts": hc.pop("mounts", [])}


def test_clean_container_has_no_violations():
    assert checks.container_violations([_ctr()]) == []


def test_each_gap_is_reported():
    got = " | ".join(checks.container_violations([
        _ctr("/a", Privileged=True),
        _ctr("/b", CapAdd=["SYS_ADMIN"]),
        _ctr("/c", SecurityOpt=["apparmor=unconfined"]),
        _ctr("/d", NetworkMode="host"),
        {"Name": "/e", "HostConfig": {"Memory": 1}, "Mounts": [{"Source": "/var/run/docker.sock"}]},
        {"Name": "/f", "HostConfig": {"Memory": 0}, "Mounts": []},
    ]))
    for needle in ("a: privileged", "b: extra capabilities", "c: confinement disabled",
                   "d: shares host", "e: mounts the Docker socket", "f: no memory limit"):
        assert needle in got


def test_check_containers_fails_on_privileged():
    def fake(cmd, timeout=10):
        out = "abc\n" if cmd[1] == "ps" else json.dumps([_ctr(Privileged=True)])
        return subprocess.CompletedProcess(cmd, 0, out, "")

    c = checks.check_containers(fake)
    assert c.status == checks.FAIL and "privileged" in c.detail
