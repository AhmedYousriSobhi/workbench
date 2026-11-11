"""`benchctl measure cgroup` (issue 455): a fake cgroup tree and a fake docker, nothing live."""
import subprocess

from typer.testing import CliRunner

from benchctl import cli, measure

MIB = 1024**2


def tree(tmp_path, cid="abc", shmem=60 * MIB, oom=0):
    d = tmp_path / "system.slice" / f"docker-{cid}.scope"
    d.mkdir(parents=True)
    (d / "memory.stat").write_text(f"anon {10 * MIB}\nfile {90 * MIB}\nshmem {shmem}\nslab 5\n")
    (d / "memory.events").write_text(f"low 0\noom_kill {oom}\n")
    (d / "memory.current").write_text(f"{150 * MIB}\n")
    (d / "memory.max").write_text("max\n")
    return d


def docker(cid="abc", rc=0):
    return lambda cmd, timeout=10: subprocess.CompletedProcess(cmd, rc, cid + "\n" if rc == 0 else "", "")


def test_reads_shmem_current_and_oom_kills(tmp_path):
    tree(tmp_path, oom=2)
    m = measure.cgroup_memory("x", docker(), tmp_path)
    assert m["shmem"] == 60 * MIB and m["current"] == 150 * MIB and m["oom_kill"] == 2
    assert m["limit"] == 0  # "max" means no limit


def test_cgroupfs_driver_path_is_found_too(tmp_path):
    d = tmp_path / "docker" / "abc"
    d.mkdir(parents=True)
    (d / "memory.stat").write_text("shmem 7\n")
    assert measure.cgroup_memory("x", docker(), tmp_path)["shmem"] == 7


def test_a_container_that_is_not_running_gives_none(tmp_path):
    assert measure.cgroup_memory("x", docker(rc=1), tmp_path) is None
    assert measure.cgroup_memory("x", docker("zzz"), tmp_path) is None  # no cgroup dir for that id


def test_cli_prints_a_row_per_container_and_fails_when_one_is_missing(tmp_path, monkeypatch):
    tree(tmp_path)
    monkeypatch.setattr(measure, "cgroup_memory", lambda n, *a:
                        ({"current": 150 * MIB, "shmem": 60 * MIB, "file": 0, "anon": 0, "limit": 300 * MIB, "oom_kill": 0}
                         if "mariadb" in n else None))
    r = CliRunner().invoke(cli.app, ["measure", "cgroup"])
    assert r.exit_code == 1
    assert "workbench-mariadb-1" in r.output and "60 MB" in r.output and "300 MB" in r.output
    assert "workbench-teleport-1" in r.output and "not running" in r.output
