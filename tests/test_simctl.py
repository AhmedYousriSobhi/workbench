import subprocess
import tarfile

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
    t = ("origin https://ghp_abcdefghijklmnopqrstuvwxyz0123@github.com/x\n"
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
