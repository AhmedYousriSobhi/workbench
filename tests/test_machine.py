import subprocess
from pathlib import Path

from simctl import machine as m


def cp(rc=0, out="", err=""):
    return subprocess.CompletedProcess([], rc, out, err)


def test_meminfo_gb_reads_total_and_available():
    t, a = m.meminfo_gb("MemTotal: 16777216 kB\nMemFree: 1 kB\nMemAvailable: 8388608 kB\n")
    assert (t, a) == (16.0, 8.0)
    assert m.meminfo_gb("nothing") == (0.0, 0.0)


def test_os_ubuntu_other_macos_windows():
    assert m.check_os("Linux", 'PRETTY_NAME="Ubuntu 24.04 LTS"\n').status == m.OK
    assert m.check_os("Linux", 'PRETTY_NAME="Fedora Linux 40"\n').status == m.WARN
    assert m.check_os("Linux", "").status == m.WARN  # no PRETTY_NAME: label falls back to Linux
    assert m.check_os("Darwin").status == m.FAIL and "agent" in m.check_os("Darwin").fix
    assert m.check_os("Windows").status == m.FAIL


def test_os_release_unreadable(monkeypatch):
    monkeypatch.setattr(Path, "read_text", lambda self: (_ for _ in ()).throw(OSError("no")))
    assert m.check_os("Linux").status == m.WARN


def test_cpu_ram_disk_python_thresholds():
    assert [m.check_cpus(n).status for n in (1, 3, 8)] == [m.FAIL, m.WARN, m.OK]
    assert [m.check_ram(g).status for g in (4, 10, 15)] == [m.FAIL, m.WARN, m.OK]
    assert [m.check_disk(free_gb=g).status for g in (5, 15, 100)] == [m.FAIL, m.WARN, m.OK]
    assert [m.check_python(v).status for v in ((3, 10), (3, 11), (3, 12))] == [m.FAIL, m.OK, m.OK]


def test_live_readers_return_a_check():
    assert m.check_cpus().name == "cpu" and m.check_ram().name == "ram"
    assert m.check_disk().name == "disk" and m.check_python().name == "python"


def test_cgroup_v2(tmp_path):
    assert m.check_cgroup_v2(tmp_path).status == m.FAIL
    (tmp_path / "cgroup.controllers").write_text("memory")
    assert m.check_cgroup_v2(tmp_path).status == m.OK


def test_docker_missing_daemon_down_and_all_good():
    assert m.check_docker(lambda c: cp(), which=lambda t: None)[0].status == m.FAIL

    def down(cmd):
        return cp(1, "", "Cannot connect to the Docker daemon\nmore") if "info" in cmd else cp(0, "2.0")
    res = m.check_docker(down, which=lambda t: "/usr/bin/docker")
    assert [c.status for c in res] == [m.OK, m.FAIL, m.OK] and "Cannot connect" in res[1].detail

    def no_compose(cmd):
        return cp(0, "27.0") if "info" in cmd else cp(1)
    assert m.check_docker(no_compose, which=lambda t: "x")[2].status == m.FAIL

    def good(cmd):
        return cp(0, "27.0" if "info" in cmd else "v2.29")
    res = m.check_docker(good, which=lambda t: "x")
    assert all(c.status == m.OK for c in res) and res[2].detail == "v2.29"


def test_run_swallows_missing_binary():
    assert m.run(["definitely-not-a-binary-xyz"]).returncode == 127


def test_ports():
    assert m.check_ports(lambda p: True).status == m.OK
    c = m.check_ports(lambda p: p != 8088)
    assert c.status == m.WARN and "8088 (web UI)" in c.detail


def test_port_free_detects_a_bound_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        assert m.port_free(port) is False
    assert m.port_free(port) is True


def test_optional_tools():
    res = m.check_optional(which=lambda t: None, kvm=False)
    assert all(c.status == m.WARN for c in res)
    res = m.check_optional(which=lambda t: "x", kvm=True)
    assert all(c.status == m.OK for c in res)
    assert m.check_optional()[0].name == "kvm"


def test_profile_budgets_and_fit(tmp_path):
    (tmp_path / "a.yaml").write_text("name: small\nbudget_gb: 1\n")
    (tmp_path / "b.yaml").write_text("name: big\nbudget_gb: 6.5\n")
    (tmp_path / "c.yaml").write_text("other: 1\n")
    b = m.profile_budgets(tmp_path)
    assert b == {"small": 1.0, "big": 6.5}
    assert "small" in m.check_profiles(3, b).detail and "big" not in m.check_profiles(3, b).detail
    assert m.check_profiles(9, b).detail.count("big") == 2
    assert m.check_profiles(1.5, b).status == m.FAIL
    assert m.check_profiles(9, {}).status == m.WARN


def test_real_profiles_include_min():
    assert "min" in m.profile_budgets()


def test_report_and_main(monkeypatch, capsys):
    good = [m.Check("a", m.OK, "fine"), m.Check("b", m.WARN, "meh", "do x")]
    out = m.report(good)
    assert "READY" in out and "fix: do x" in out and "NOT READY" not in out
    bad = good + [m.Check("c", m.FAIL, "no", "do y")]
    assert "NOT READY: 1" in m.report(bad)
    monkeypatch.setattr(m, "run_all", lambda: good)
    assert m.main() == 0
    monkeypatch.setattr(m, "run_all", lambda: bad)
    assert m.main() == 1
    assert "READY" in capsys.readouterr().out


def test_run_all_on_this_host_returns_checks():
    res = m.run_all()
    assert res and all(isinstance(c, m.Check) for c in res)


def test_run_all_off_linux(monkeypatch):
    monkeypatch.setattr(m.platform, "system", lambda: "Darwin")
    names = [c.name for c in m.run_all()]
    assert "os" in names and "cgroup v2" not in names and "profiles" not in names


def test_cli_check(monkeypatch):
    from typer.testing import CliRunner

    from simctl import cli
    monkeypatch.setattr(m, "run_all", lambda: [m.Check("a", m.OK, "fine")])
    r = CliRunner().invoke(cli.app, ["check"])
    assert r.exit_code == 0 and "READY" in r.stdout
    monkeypatch.setattr(m, "run_all", lambda: [m.Check("a", m.FAIL, "no")])
    assert CliRunner().invoke(cli.app, ["check"]).exit_code == 1
