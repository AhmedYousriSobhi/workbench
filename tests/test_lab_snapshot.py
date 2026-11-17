"""Config lab snapshot and restore (issue 554 step 2): the command sequence, the hash compare and the image's restore-state script,
against a fake Docker. No container is started."""
import hashlib
import io
import json
import os
import subprocess
import tarfile
from pathlib import Path

import pytest

from benchctl import lab_snapshot as ls
from benchctl.lab import LabError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "images" / "slurm-ctl" / "restore-state.sh"
SPOOL = {"job_state": b"jobs v1", "node_state": b"nodes", "hash.3/job.103/script": b"#!/bin/sh\nsleep 1\n", "jwt_hs256.key": b"secret"}
DUMP = b"CREATE TABLE `job_table` (id int) ENGINE=InnoDB AUTO_INCREMENT=120;\nINSERT INTO `job_table` VALUES (1),(2);\n"


def cp(rc=0, out=b"", err=b""):
    return subprocess.CompletedProcess([], rc, out, err)


def spool_tar(files: dict[str, bytes], prefix="slurmctld/") -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        t.addfile(_dir(prefix.rstrip("/")))
        dirs = sorted({str(Path(p).parent) for p in files if "/" in p})
        for d in dirs:
            t.addfile(_dir(prefix + d))
        for p, data in files.items():
            ti = tarfile.TarInfo(prefix + p)
            ti.size = len(data)
            t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _dir(name):
    ti = tarfile.TarInfo(name)
    ti.type = tarfile.DIRTYPE
    ti.mode = 0o755
    return ti


def untar(data: bytes) -> dict[str, bytes]:
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        return {m.name: t.extractfile(m).read() for m in t.getmembers() if m.isfile()}


class FakeDocker:
    """Answers the docker and compose calls a snapshot and restore make, and plays restore-state.sh at slurmctld's start."""

    def __init__(self, spool=None, db=DUMP):
        self.spool = dict(spool or SPOOL)
        self.db, self.up = db, {"slurmctld": True, "slurmdbd": True}
        self.restore_tar, self.sums, self.fail, self.calls, self.loads = None, None, set(), [], []
        self.corrupt_restore = self.corrupt_db = False

    def __call__(self, cmd, input=None):
        self.calls.append(cmd)
        line = " ".join(cmd)
        if any(f in line for f in self.fail):
            return cp(1, err=b"boom\n")
        if cmd[:2] == ["docker", "compose"]:
            verb, rest = cmd[6], cmd[7:]
            if verb in ("stop", "start"):
                self.up[rest[0]] = verb == "start"
                if verb == "start" and rest[0] == "slurmctld" and self.restore_tar:
                    self._restore_state()
                return cp()
            if verb == "exec" and rest[1] == "mariadb":
                if rest[-1] == ls.DUMP_SH:
                    return cp(out=self.db)
                assert rest[-1] == ls.LOAD_SH and not self.up["slurmdbd"], "the database is reloaded with slurmdbd stopped"
                self.loads.append(input)
                self.db = input.replace(b"AUTO_INCREMENT=120", b"AUTO_INCREMENT=3")  # a reload resets the counters
                if self.corrupt_db:
                    self.db += b"INSERT INTO `job_table` VALUES (9);\n"
                return cp()
            if verb == "exec" and rest[1] == "slurmctld":
                return cp(out=b"JobID|JobName|User\n7|j0001|lab-u001\n")
        if cmd[:2] == ["docker", "cp"]:
            assert not self.up["slurmctld"], "docker cp runs while slurmctld is stopped"
            if cmd[2] == f"{ls.CTLD}:{ls.SPOOL}":
                return cp(out=spool_tar(self.spool))
            assert cmd[3] == f"{ls.CTLD}:{ls.RESTORE_TAR}"
            self.restore_tar = Path(cmd[2]).read_bytes()
            return cp()
        if cmd[:2] == ["docker", "exec"]:
            if self.restore_tar is None and self.sums is not None:
                return cp(out=self.sums.encode())
            return cp(1)
        raise AssertionError(f"unexpected call {cmd}")

    def _restore_state(self):
        files = untar(self.restore_tar)
        if self.corrupt_restore:
            files["job_state"] = b"tampered"
        self.spool = {"jwt_hs256.key": self.spool["jwt_hs256.key"], **files}
        self.sums = "".join(f"{hashlib.sha256(d).hexdigest()}  ./{p}\n" for p, d in files.items())
        self.restore_tar = None


def cluster(fake, **kw):
    return ls.Cluster(runner=fake, sleep=lambda s: None, **kw)


# ---------------------------------------------------------------- tar and hashes

def test_filter_tar_strips_the_prefix_and_leaves_out_the_jwt_key():
    data, manifest = ls.filter_tar(spool_tar(SPOOL))
    assert set(manifest) == {"job_state", "node_state", "hash.3/job.103/script"}
    assert untar(data) == {k: v for k, v in SPOOL.items() if k != "jwt_hs256.key"}
    assert manifest["job_state"] == hashlib.sha256(b"jobs v1").hexdigest()


def test_filter_tar_skips_odd_members():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as t:
        for name, typ in (("slurmctld/fifo", tarfile.FIFOTYPE), ("slurmctld/../evil", tarfile.REGTYPE), ("slurmctld/ok", tarfile.REGTYPE)):
            ti = tarfile.TarInfo(name)
            ti.type = typ
            t.addfile(ti, io.BytesIO(b""))
        link = tarfile.TarInfo("slurmctld/link")
        link.type, link.linkname = tarfile.SYMTYPE, "ok"
        t.addfile(link)
    data, manifest = ls.filter_tar(buf.getvalue())
    assert manifest == {"ok": hashlib.sha256(b"").hexdigest()}
    with tarfile.open(fileobj=io.BytesIO(data)) as t:
        assert sorted(m.name for m in t.getmembers()) == ["link", "ok"]


def test_parse_sums_and_differences():
    sums = ls.parse_sums("aa  ./a\nbb  ./d/b\n\nbroken\n")
    assert sums == {"a": "aa", "d/b": "bb"}
    assert ls.differences({"a": "aa", "c": "cc", "d/b": "xx"}, {**sums, "e": "ee"}) == {"missing": 1, "extra": 1, "changed": 1}


def test_db_hash_ignores_auto_increment_only():
    assert ls.db_hash(DUMP) == ls.db_hash(DUMP.replace(b"=120", b"=3"))
    assert ls.db_hash(DUMP) != ls.db_hash(DUMP + b"x")


# ---------------------------------------------------------------- snapshot and restore

def test_snapshot_then_restore_compares_equal(tmp_path):
    fake = FakeDocker()
    c = cluster(fake)
    dest = tmp_path / "snap"
    s = ls.snapshot(dest, c)
    assert s["spool_files"] == 3 and s["db_sha256"] == ls.db_hash(DUMP)
    assert dest.stat().st_mode & 0o777 == 0o700
    assert (dest / "accounting.sql").read_bytes() == DUMP and "jwt" not in (dest / "snapshot.json").read_text()
    assert fake.up["slurmctld"]
    # run A changes the state
    fake.spool["job_state"] = b"jobs v2"
    fake.spool["hash.4/job.204/script"] = b"later"
    fake.db = DUMP + b"INSERT INTO `job_table` VALUES (3);\n"
    r = ls.restore(dest, c)
    assert r == {"spool_match": True, "db_match": True, "spool_files": 3, "spool_sha256": s["spool_sha256"],
                 "missing": 0, "extra": 0, "changed": 0}
    assert fake.spool == SPOOL and fake.loads == [DUMP] and fake.up == {"slurmctld": True, "slurmdbd": True}


def test_restore_order(tmp_path):
    fake = FakeDocker()
    c = cluster(fake)
    ls.snapshot(tmp_path, c)
    fake.calls.clear()
    ls.restore(tmp_path, c)
    steps = [" ".join(x[6:8]) if x[1] == "compose" and x[6] != "exec" else x[1] if x[1] != "compose" else x[8] for x in fake.calls]
    assert steps == ["stop slurmctld", "stop slurmdbd", "mariadb", "mariadb", "start slurmdbd", "cp", "start slurmctld", "exec"]


def test_snapshot_without_accounting(tmp_path):
    fake = FakeDocker()
    s = ls.snapshot(tmp_path, cluster(fake, accounting=False))
    assert s["db_sha256"] is None and not (tmp_path / "accounting.sql").exists()
    r = ls.restore(tmp_path, cluster(fake, accounting=False))
    assert r["spool_match"] and r["db_match"] is None
    assert not any("mariadb" in x for x in fake.calls[-4:])


def test_restore_with_accounting_snapshot_needs_accounting(tmp_path):
    fake = FakeDocker()
    ls.snapshot(tmp_path, cluster(fake))
    with pytest.raises(LabError, match="accounting is not running"):
        ls.restore(tmp_path, cluster(fake, accounting=False))
    assert fake.up["slurmctld"]  # started again after the refusal


def test_restore_refuses_a_differing_spool(tmp_path):
    fake = FakeDocker()
    c = cluster(fake)
    ls.snapshot(tmp_path, c)
    fake.corrupt_restore = True
    with pytest.raises(LabError, match=r"slurmctld state \(0 missing, 0 extra, 1 changed"):
        ls.restore(tmp_path, c)


def test_restore_refuses_a_differing_database(tmp_path):
    fake = FakeDocker()
    c = cluster(fake)
    ls.snapshot(tmp_path, c)
    fake.corrupt_db = True
    with pytest.raises(LabError, match="accounting database differ"):
        ls.restore(tmp_path, c)
    assert fake.up["slurmdbd"] and fake.up["slurmctld"]


def test_restore_without_snapshot(tmp_path):
    with pytest.raises(LabError, match="no snapshot"):
        ls.restore(tmp_path, cluster(FakeDocker()))


def test_restore_times_out_when_the_controller_never_reports(tmp_path):
    fake = FakeDocker()
    c = cluster(fake, wait_s=6)
    ls.snapshot(tmp_path, c)
    fake.fail.add(f"cat {ls.RESTORED_SUMS}")
    with pytest.raises(LabError, match="did not report the restored state within 6 s"):
        ls.restore(tmp_path, c)
    assert sum(1 for x in fake.calls if x[:2] == ["docker", "exec"]) == 3


def test_snapshot_failure_starts_slurmctld_again(tmp_path):
    fake = FakeDocker()
    fake.fail.add("docker cp")
    with pytest.raises(LabError, match="copying the slurmctld state failed: boom"):
        ls.snapshot(tmp_path, cluster(fake))
    assert fake.up["slurmctld"] and not (tmp_path / "snapshot.json").exists()


def test_snapshot_of_an_empty_spool_is_refused(tmp_path):
    fake = FakeDocker(spool={"jwt_hs256.key": b"k"})
    with pytest.raises(LabError, match="state is empty"):
        ls.snapshot(tmp_path, cluster(fake))


def test_must_reports_exit_code_without_stderr():
    c = ls.Cluster(runner=lambda cmd: cp(3))
    with pytest.raises(LabError, match="x failed: exit 3"):
        c.must(["true"], "x")
    assert ls.Cluster(runner=lambda cmd: subprocess.CompletedProcess(cmd, 0, "text", "")).must(["t"], "t") == b"text"


def test_no_password_in_any_command(tmp_path):
    fake = FakeDocker()
    c = cluster(fake)
    ls.snapshot(tmp_path, c)
    ls.restore(tmp_path, c)
    for cmd in fake.calls:
        for part in cmd:
            assert "MARIADB_ROOT_PASSWORD" not in part or part in (ls.DUMP_SH, ls.LOAD_SH)  # expanded inside the container only


def test_discard(tmp_path):
    d = tmp_path / "snap"
    d.mkdir()
    (d / "x").write_text("1")
    ls.discard(d)
    assert not d.exists()
    ls.discard(d)  # twice is fine


def test_snapshot_dir_is_outside_the_reports(tmp_path):
    p = ls.snapshot_dir("prod-a", "20261117-120000", tmp_path)
    assert p == tmp_path / "prod-a" / "snapshots" / "20261117-120000" and "reports" not in p.parts


# ---------------------------------------------------------------- run A's accounting

def test_collect_sacct_asks_for_the_lab_users_only():
    fake = FakeDocker()
    out = ls.collect_sacct(["lab-u001", "lab-u002"], 3600.4, cluster(fake))
    assert out == {"sacct.txt": "JobID|JobName|User\n7|j0001|lab-u001\n"}
    cmd = fake.calls[-1]
    assert cmd[cmd.index("-u") + 1] == "lab-u001,lab-u002" and cmd[cmd.index("-S") + 1] == "now-3660"
    assert cmd[cmd.index("-o") + 1] == ls.SACCT_FIELDS and "-a" not in cmd


def test_collect_sacct_refusals():
    with pytest.raises(LabError, match="no lab user"):
        ls.collect_sacct([], 1, cluster(FakeDocker()))
    with pytest.raises(LabError, match="accounting is not running"):
        ls.collect_sacct(["lab-u001"], 1, cluster(FakeDocker(), accounting=False))


# ---------------------------------------------------------------- with the run's state machine

def test_state_steps_drive_a_full_ab_run(tmp_path):
    import yaml

    from benchctl import lab, lab_run

    sim = yaml.safe_load((ROOT / "sim.yaml").read_text())
    root = tmp_path / "lab"
    lab.import_lab("prod-a", ROOT / "tests" / "fixtures" / "lab" / "full", root=root)
    t = [1_800_000_000.0]
    run = lab_run.Run.create("prod-a", sim, root, clock=lambda: t[0])
    fake = FakeDocker()
    state = ls.StateSteps(ls.snapshot_dir("prod-a", run.r["run"], root), cluster(fake))

    class Steps:
        snapshot, restore, collect, discard = state.snapshot, state.restore, state.collect, state.discard

        def abort_reason(self):
            return None

        def apply(self, side):
            pass

        def replay(self, side, run):
            t[0] += 600
            fake.spool["job_state"] = f"after {side}".encode()  # the replay changes the state

        def compare(self, run):
            pass

        def report(self, run):
            pass

    lab_run.drive(run, Steps())
    assert run.state == "done" and run.r["restore"]["spool_match"] and run.r["restore"]["db_match"]
    sacct = [c for c in fake.calls if "sacct" in c]
    assert len(sacct) == 2 and sacct[0][sacct[0].index("-S") + 1] == "now-660"
    assert (run.side_dir("a") / "sacct.txt").read_text().startswith("JobID|JobName|User")
    assert not state.dest.exists()  # discarded when the run ended
    with pytest.raises(LabError, match="has not been in"):
        run.entered("paused")


# ---------------------------------------------------------------- the image's restore-state script, run for real in a temp folder

def _script(tmp_path, tar_bytes=None):
    spool, tarf, sums = tmp_path / "spool", tmp_path / "restore.tar", tmp_path / "restored.sha256"
    spool.mkdir(exist_ok=True)
    if tar_bytes is not None:
        tarf.write_bytes(tar_bytes)
    env = {**os.environ, "WORKBENCH_SPOOL": str(spool), "WORKBENCH_RESTORE_TAR": str(tarf), "WORKBENCH_RESTORED_SUMS": str(sums)}
    r = subprocess.run(["sh", str(SCRIPT)], env=env, capture_output=True, text=True, check=False)
    return r, spool, tarf, sums


def test_restore_state_script_without_a_tar_does_nothing(tmp_path):
    r, _, _, sums = _script(tmp_path)
    assert r.returncode == 0 and not sums.exists()


def test_restore_state_script_matches_the_python_manifest(tmp_path):
    data, manifest = ls.filter_tar(spool_tar(SPOOL))
    spool = tmp_path / "spool"
    (spool / "hash.9").mkdir(parents=True)
    (spool / "hash.9" / "stale").write_text("from run A")
    (spool / "job_state").write_text("run A")
    (spool / "jwt_hs256.key").write_text("installed at start")
    r, spool, tarf, sums = _script(tmp_path, data)
    assert r.returncode == 0, r.stderr
    assert not tarf.exists() and not (spool / "hash.9").exists()
    assert (spool / "jwt_hs256.key").read_text() == "installed at start"
    assert (spool / "job_state").read_bytes() == b"jobs v1"
    assert ls.parse_sums(sums.read_text()) == manifest
    assert ls.manifest_hash(ls.parse_sums(sums.read_text())) == ls.manifest_hash(manifest)


def test_image_runs_restore_state_before_slurmctld():
    text = (ROOT / "images" / "slurm-ctl" / "Dockerfile").read_text()
    cmd = json.loads(text.split("CMD ", 1)[1].splitlines()[0])
    assert cmd[2].startswith("/usr/local/sbin/restore-state || exit 1; ") and cmd[2].endswith("exec slurmctld -D")
    assert "restore-state.sh /usr/local/sbin/restore-state" in text


# ---------------------------------------------------------------- a restore cut off part way can be run again (round 3, Q1)

@pytest.mark.parametrize("cut", [ls.LOAD_SH, f"{ls.CTLD}:{ls.RESTORE_TAR}", ls.RESTORED_SUMS],
                         ids=["database reload fails", "spool copy fails", "controller never reports"])
def test_a_restore_cut_off_at_any_point_succeeds_when_run_again(tmp_path, cut):
    """The governor may die, or a step fail, anywhere in a restore: a restore-only child then runs `restore` again from the start.
    Each cut-off point leaves something different (database half loaded, controller stopped with no tar, tar copied but no sums);
    the second run must reproduce the snapshot all the same."""
    fake = FakeDocker()
    c = cluster(fake, wait_s=4)
    dest = tmp_path / "snap"
    s = ls.snapshot(dest, c)
    fake.spool["job_state"] = b"run A"
    fake.db = DUMP + b"INSERT INTO `job_table` VALUES (3);\n"
    fake.fail = {cut}
    with pytest.raises(LabError):
        ls.restore(dest, c)
    if cut == ls.LOAD_SH:
        fake.db = b"half a dump"  # the reload stopped part way
    fake.fail = set()
    r = ls.restore(dest, c)
    assert r["spool_match"] and r["db_match"] and r["spool_sha256"] == s["spool_sha256"]
    assert fake.spool == SPOOL and fake.up == {"slurmctld": True, "slurmdbd": True}
