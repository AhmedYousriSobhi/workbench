"""Config lab snapshot and restore of the cluster state around an A/B run (issue 554 step 2, ADR 0050, owner decision 3).

Before run A the run saves slurmctld's `StateSaveLocation` (a tar of /var/spool/slurmctld) and a dump of the accounting database;
before run B it puts both back, so the two runs start from the same state. The restore is internal to the run (`lab_run.drive`),
never a governor verb (decision 3), and it erases run A's accounting: `lab_run.Run.require_collected` refuses it until
`collect_sacct` has written that accounting to the report folder.

How (the controller container runs `exec slurmctld -D`, so nothing can be exec'd into it while slurmctld is stopped):
- snapshot: `compose stop slurmctld` (slurmctld writes its state on the way down), `docker cp` the spool out as a tar,
  `mariadb-dump` the accounting database, `compose start slurmctld`.
- restore: stop slurmctld and slurmdbd, drop and reload the database, dump it again and compare, start slurmdbd, `docker cp` the
  tar into the stopped controller as /var/spool/slurmctld.restore.tar, start it. The image's `restore-state` (run before slurmctld
  in the container's command) empties the spool, extracts the tar, writes the sha256 of every file, then removes the tar.
- compare: per-file sha256 of the spool (from the tar here, from `restore-state` there) and the sha256 of the dump with the
  `AUTO_INCREMENT=` counters removed (a reload sets them to the highest row plus one, not to the value the original had).

The JWT key in the spool is never saved or compared: it is installed at every start from the keys `benchctl up` makes (#465).
Passwords stay inside the mariadb container (`$MARIADB_ROOT_PASSWORD` in its environment), never in argv or output.
The snapshot holds the accounting database, which may name production users after `apply lab` (decision 5's dump rewrite is not
built yet), so it lives under compose/generated/lab/NAME/snapshots/RUN (gitignored, mode 700), never in the report folder, and
`discard` deletes it when the run ends.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import lab
from .lab import LabError

PROJECT = "workbench"
CTLD = f"{PROJECT}-slurmctld-1"
SPOOL = "/var/spool/slurmctld"
RESTORE_TAR = "/var/spool/slurmctld.restore.tar"  # read by images/slurm-ctl/restore-state.sh at container start
RESTORED_SUMS = "/var/spool/slurmctld.restored.sha256"
NOT_SAVED = ("jwt_hs256.key",)
DB = "slurm_acct_db"
_ROOT_SQL = 'mariadb -uroot -p"$MARIADB_ROOT_PASSWORD"'
DUMP_SH = f'exec mariadb-dump -uroot -p"$MARIADB_ROOT_PASSWORD" --single-transaction --skip-dump-date --skip-comments --routines --triggers {DB}'
LOAD_SH = f'{_ROOT_SQL} -e "DROP DATABASE IF EXISTS {DB}; CREATE DATABASE {DB}" && exec {_ROOT_SQL} {DB}'
SACCT_FIELDS = "JobID,JobName,User,Account,QOS,Partition,AllocTRES,NNodes,Timelimit,Submit,Eligible,Start,End,Elapsed,State,ExitCode"
_AUTO_INC = re.compile(rb" AUTO_INCREMENT=\d+")


def _run(cmd: list[str], input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, input=input, capture_output=True, check=False, timeout=600)


@dataclass
class Cluster:
    """The calls a snapshot and restore make, injectable so the unit tests need no Docker."""

    runner: Callable[..., subprocess.CompletedProcess] = _run
    accounting: bool = True
    sleep: Callable[[float], None] = time.sleep
    wait_s: int = 120
    calls: list[list[str]] = field(default_factory=list)

    def dc(self, *args: str) -> list[str]:
        from . import stack
        return stack.dc(*args)

    def must(self, cmd: list[str], what: str, input: bytes | None = None) -> bytes:
        self.calls.append(cmd)
        r = self.runner(cmd, input) if input is not None else self.runner(cmd)
        if r.returncode != 0:
            err = r.stderr.decode(errors="replace") if isinstance(r.stderr, bytes) else str(r.stderr or "")
            raise LabError(f"{what} failed: {err.strip()[-200:] or 'exit ' + str(r.returncode)}")
        return r.stdout if isinstance(r.stdout, bytes) else (r.stdout or "").encode()


def snapshot_dir(name: str, run_id: str, root: Path | None = None) -> Path:
    return lab.path(name, root) / "snapshots" / run_id


# ---------------------------------------------------------------- spool tar and hashes

def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def filter_tar(raw: bytes) -> tuple[bytes, dict[str, str]]:
    """`docker cp CTR:/var/spool/slurmctld -` gives a tar under `slurmctld/`. Returns a tar of the spool's contents (paths relative to
    the spool, the JWT key left out) and {path: sha256} of its regular files."""
    out, manifest = io.BytesIO(), {}
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, tarfile.open(fileobj=out, mode="w") as dst:
        for m in src.getmembers():
            parts = m.name.lstrip("./").split("/", 1)
            if len(parts) < 2 or not parts[1] or parts[1] in NOT_SAVED:
                continue
            rel = parts[1]
            if ".." in rel.split("/") or not (m.isfile() or m.isdir() or m.issym()):
                continue
            m.name = rel
            if m.isfile():
                data = src.extractfile(m).read()
                manifest[rel] = _sha(data)
                dst.addfile(m, io.BytesIO(data))
            else:
                dst.addfile(m)
    return out.getvalue(), manifest


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` lines (`HASH  ./path`) as {path: hash}."""
    out = {}
    for line in text.splitlines():
        h, _, p = line.partition("  ")
        if h and p:
            out[p.removeprefix("./")] = h
    return out


def manifest_hash(manifest: dict[str, str]) -> str:
    return _sha("".join(f"{h}  {p}\n" for p, h in sorted(manifest.items())).encode())


def db_hash(dump: bytes) -> str:
    return _sha(_AUTO_INC.sub(b"", dump))


def differences(want: dict[str, str], got: dict[str, str]) -> dict[str, int]:
    return {"missing": len(set(want) - set(got)), "extra": len(set(got) - set(want)),
            "changed": sum(1 for p in set(want) & set(got) if want[p] != got[p])}


# ---------------------------------------------------------------- snapshot, restore, discard

def snapshot(dest: Path, c: Cluster) -> dict:
    """Save the spool and the accounting database to `dest`; returns the hashes the restore is compared with."""
    dest.mkdir(parents=True, exist_ok=True)
    dest.chmod(0o700)
    c.must(c.dc("stop", "slurmctld"), "stopping slurmctld")  # it saves its state on the way down
    try:
        raw = c.must(["docker", "cp", f"{CTLD}:{SPOOL}", "-"], "copying the slurmctld state")
        sql = c.must(c.dc("exec", "-T", "mariadb", "sh", "-c", DUMP_SH), "dumping the accounting database") if c.accounting else None
    finally:
        c.must(c.dc("start", "slurmctld"), "starting slurmctld")
    data, manifest = filter_tar(raw)
    if not manifest:
        raise LabError("the slurmctld state is empty: is the cluster up (benchctl up)?")
    (dest / "spool.tar").write_bytes(data)
    out = {"spool_files": len(manifest), "spool_sha256": manifest_hash(manifest), "db_sha256": None}
    if sql is not None:
        (dest / "accounting.sql").write_bytes(sql)
        out["db_sha256"] = db_hash(sql)
    (dest / "snapshot.json").write_text(json.dumps({**out, "manifest": manifest}, indent=2, sort_keys=True) + "\n")
    return out


def _restored_sums(c: Cluster) -> dict[str, str]:
    """The sums `restore-state` wrote, once the restore tar is gone (it removes the tar last, so the sums are this restore's)."""
    cmd = ["docker", "exec", CTLD, "sh", "-c", f"test ! -e {RESTORE_TAR} && cat {RESTORED_SUMS}"]
    for _ in range(max(1, c.wait_s // 2)):
        c.calls.append(cmd)
        r = c.runner(cmd)
        if r.returncode == 0:
            out = r.stdout.decode() if isinstance(r.stdout, bytes) else r.stdout
            return parse_sums(out)
        c.sleep(2)
    raise LabError(f"slurmctld did not report the restored state within {c.wait_s} s; see `docker logs {CTLD}`")


def restore(dest: Path, c: Cluster) -> dict:
    """Put the snapshot in `dest` back and compare it with what the cluster now holds; refuses on any difference."""
    try:
        snap = json.loads((dest / "snapshot.json").read_text())
    except (OSError, ValueError) as e:
        raise LabError("no snapshot to restore (it is taken before run A)") from e
    db_match = None
    c.must(c.dc("stop", "slurmctld"), "stopping slurmctld")
    try:
        if snap.get("db_sha256"):
            if not c.accounting:
                raise LabError("the snapshot holds accounting, but accounting is not running: start it (benchctl up --with accounting)")
            c.must(c.dc("stop", "slurmdbd"), "stopping slurmdbd")
            try:
                c.must(c.dc("exec", "-T", "mariadb", "sh", "-c", LOAD_SH), "reloading the accounting database",
                       input=(dest / "accounting.sql").read_bytes())
                again = c.must(c.dc("exec", "-T", "mariadb", "sh", "-c", DUMP_SH), "dumping the restored accounting database")
            finally:
                c.must(c.dc("start", "slurmdbd"), "starting slurmdbd")
            db_match = db_hash(again) == snap["db_sha256"]
        c.must(["docker", "cp", str(dest / "spool.tar"), f"{CTLD}:{RESTORE_TAR}"], "copying the slurmctld state back")
    finally:
        c.must(c.dc("start", "slurmctld"), "starting slurmctld")
    got = _restored_sums(c)
    diff = differences(snap["manifest"], got)
    spool_match = not any(diff.values())
    out = {"spool_match": spool_match, "db_match": db_match, "spool_files": len(got), "spool_sha256": manifest_hash(got), **diff}
    if not spool_match or db_match is False:
        what = [f"slurmctld state ({diff['missing']} missing, {diff['extra']} extra, {diff['changed']} changed file(s))"] if not spool_match else []
        what += ["accounting database"] if db_match is False else []
        raise LabError("the restore did not reproduce the snapshot: " + " and ".join(what) + " differ; run B would not start from run A's state")
    return out


def discard(dest: Path) -> None:
    """Delete a snapshot when its run has ended (it may hold production names in the accounting dump)."""
    shutil.rmtree(dest, ignore_errors=True)


# ---------------------------------------------------------------- run A's accounting, before the restore

def filter_rows(text: str, users: set[str], keys: set[str]) -> str:
    """A `sacct -P` text (header first) cut to the rows of the lab users or of the run's job keys (the job name)."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return ""
    head = lines[0].split("|")
    iu, ij = head.index("User") if "User" in head else None, head.index("JobName") if "JobName" in head else None

    def ours(row: list[str]) -> bool:
        return (iu is not None and iu < len(row) and row[iu] in users) or (ij is not None and ij < len(row) and row[ij] in keys)
    return "\n".join([lines[0]] + [ln for ln in lines[1:] if ours(ln.split("|"))]) + "\n"


def collect_sacct(users: list[str], since_s: float, c: Cluster, keys: list[str] | None = None) -> dict[str, str]:
    """The side's jobs from accounting: an allow-listed `sacct` export covering the last `since_s` seconds (Slurm's `now-N` form, so
    the container's time zone does not matter), cut to the lab users and the run's job keys. Returns {file name: text} for
    `Run.record_collected`.

    `sacct -a`, not `-u lab-u001,...`: sacct turns every `-u` name into a uid on the host it runs on (slurmctld here), and an
    unknown name (or a number with no passwd entry) is `Invalid user id` and exit 1 (Slurm 26.05.5, `src/sacct/options.c` and
    `_convert_to_id` in `src/common/slurm_protocol_defs.c`, issue 554 item 7). The rows are filtered here instead."""
    if not users:
        raise LabError("the jobs file names no lab user")
    if not c.accounting:
        raise LabError("accounting is not running: a run needs it to compare the sides (benchctl up --with accounting)")
    out = c.must(c.dc("exec", "-T", "slurmctld", "sacct", "-a", "-X", "-P", "-S", f"now-{int(since_s) + 60}",
                      "-E", "now", "-o", SACCT_FIELDS), "sacct")
    return {"sacct.txt": filter_rows(out.decode(), set(users), set(keys or []))}


@dataclass
class StateSteps:
    """The cluster-state steps of `lab_run.drive` for one run: snapshot, restore, collect (since the side's replay began), discard.
    Step 4 (the governor's run thread) adds apply, replay, compare, report and abort_reason."""

    dest: Path
    cluster: Cluster = field(default_factory=Cluster)

    def snapshot(self) -> dict:
        return snapshot(self.dest, self.cluster)

    def restore(self) -> dict:
        return restore(self.dest, self.cluster)

    def collect(self, side: str, run) -> dict[str, str]:
        doc = run.jobs()
        return collect_sacct(doc["users"], run.clock() - run.entered(f"replay_{side}"), self.cluster, [j["key"] for j in doc["jobs"]])

    def discard(self) -> None:
        discard(self.dest)
