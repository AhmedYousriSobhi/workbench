#!/usr/bin/env python3
"""Config lab replay driver (issue 554 step 3, ADR 0050, owner decision 1): one side of a run, in an unlabelled P5 one-shot container.

The host side (`benchctl/lab_steps.py`) starts it with `docker run --rm` on the workbench network, `--memory 250m`, NO
`workbench.class` label (the governor would freeze a labelled P5 container at L3 before it could abort), the slurm-ctl image,
and these read-only mounts: the frozen jobs file at /run/lab/jobs.json, slurm.conf at /etc/slurm, the munge key at
/run/workbench/slurm and the governor's flags directory at /run/workbench-flags. It is mounted from the checkout, not baked
into the image, so a change needs no rebuild. Standard library only.

What it does:
- installs the munge key and starts munged (as the cluster's own containers do), creates the run's `lab-uNNN` users with a uid
  derived from the name (`lab_uid`) so every container that creates them agrees;
- replays the jobs at their lab times: each is `runuser -u lab-uNNN -- sbatch --job-name=KEY ... --wrap "sleep S; exit C"`
  (the key pairs A with B; a timeout sleeps past its limit, an out-of-memory job exits 137);
- the replay clock stops while `lab.paused` exists (the governor's L2 flag) and the driver ends at once, cancelling its jobs,
  when `lab.abort` exists (L3 or `benchctl stop lab`). It never reads `arrivals.paused` (L1): a lab run is not paused at L1;
- prints `@progress {json}` every few seconds and one `@done {json}` at the end, on stdout; the host turns them into the run's
  progress (no file is written into a bind mount, so nothing on the host becomes root-owned).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable

FLAGS = "/run/workbench-flags"
PAUSE_FLAG, ABORT_FLAG = "lab.paused", "lab.abort"  # never `arrivals.paused`: that is L1's flag, and a run keeps going at L1
UID_BASE = 30000  # lab-u001 -> 30001: above the simulated users' uids, the same in every container that creates them
OOM_EXIT = 137
TIMEOUT_GRACE_S = 60  # a timeout sleeps this long past its limit, so Slurm ends it with TIMEOUT
PROGRESS_EVERY_S = 5.0
USER = re.compile(r"lab-u(\d{3,6})")
SAFE = re.compile(r"[A-Za-z0-9_.+:-]{1,64}")  # an account, QOS, partition or GRES value passed to sbatch (argv, no shell)

Runner = Callable[[list[str]], tuple[int, str]]


def lab_uid(user: str) -> int:
    """`lab-u001` -> 30001. Anything else is refused: the jobs file holds lab users only (decision 5)."""
    m = USER.fullmatch(user)
    if not m:
        raise ValueError(f"not a lab user name: {user!r}")
    return UID_BASE + int(m.group(1))


def run_cmd(argv: list[str]) -> tuple[int, str]:
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as e:
        return 127, str(e)
    return r.returncode, (r.stdout or "") + (r.stderr or "")


def _mb(text: str) -> int | None:
    m = re.fullmatch(r"([0-9.]+)([KMGT]?)", text.strip().upper())
    if not m:
        return None
    return math.ceil(float(m.group(1)) * {"K": 1 / 1024, "": 1, "M": 1, "G": 1024, "T": 1024 * 1024}[m.group(2)])


def tres_args(tres: str, nodes: int) -> list[str]:
    """sbatch options from a TRES string such as `cpu=8,mem=32G,node=2,gres/gpu:a100=4`: CPUs and memory per node, and GRES
    (typed GRES win over the untyped total of the same name). Unknown or unreadable parts are left out."""
    nodes = max(1, nodes)
    cpu = mem = None
    typed: dict[str, list[str]] = {}
    plain: dict[str, int] = {}
    for part in (tres or "").split(","):
        k, _, v = part.partition("=")
        k, v = k.strip(), v.strip()
        if k == "cpu" and v.isdigit():
            cpu = int(v)
        elif k == "mem":
            mem = _mb(v)
        elif k.startswith("gres/") and v.isdigit() and int(v) > 0:
            name, _, typ = k.removeprefix("gres/").partition(":")
            if not SAFE.fullmatch(name) or (typ and not SAFE.fullmatch(typ)):
                continue
            if typ:
                typed.setdefault(name, []).append(f"{name}:{typ}:{math.ceil(int(v) / nodes)}")
            else:
                plain[name] = math.ceil(int(v) / nodes)
    out = []
    if cpu:
        out.append(f"--cpus-per-task={math.ceil(cpu / nodes)}")
    if mem:
        out.append(f"--mem={math.ceil(mem / nodes)}M")
    gres = [g for name in sorted(set(typed) | set(plain)) for g in (typed.get(name) or [f"{name}:{plain[name]}"])]
    if gres:
        out.append("--gres=" + ",".join(gres))
    return out


def body(job: dict) -> str:
    """The job script: `sleep S; exit C`, built from whole numbers only. A timeout sleeps past its limit; OOM exits 137."""
    run_s = max(0, int(job.get("run_s") or 0))
    code = OOM_EXIT if job.get("kind") == "oom" else max(0, min(255, int(job.get("exit_code") or 0)))
    if job.get("kind") == "timeout" and job.get("limit_s"):
        return f"sleep {limit_minutes(job['limit_s']) * 60 + TIMEOUT_GRACE_S}; exit 0"
    return f"sleep {run_s}; exit {code}"


def limit_minutes(limit_s: float) -> int:
    """Slurm keeps time limits in minutes: a scaled limit rounds up, never below one minute."""
    return max(1, math.ceil(float(limit_s) / 60))


def sbatch_argv(job: dict) -> list[str]:
    """`runuser -u USER -- sbatch ...` for one job of the jobs file."""
    lab_uid(job["user"])  # refuses any name that is not lab-uNNN
    args = ["sbatch", "--parsable", f"--job-name={job['key']}", "--chdir=/tmp", "--output=/dev/null", "--error=/dev/null",
            f"--nodes={max(1, int(job.get('nodes') or 1))}", "--ntasks-per-node=1"]
    for opt, key in (("--account", "account"), ("--qos", "qos"), ("--partition", "partition")):
        v = job.get(key) or ""
        if v and SAFE.fullmatch(v):
            args.append(f"{opt}={v}")
    if job.get("limit_s"):
        args.append(f"--time={limit_minutes(job['limit_s'])}")
    args += tres_args(job.get("tres") or "", int(job.get("nodes") or 1))
    args += ["--wrap", body(job)]
    return ["runuser", "-u", job["user"], "--", *args]


def setup(users: list[str], run: Runner) -> list[str]:
    """Munge and the lab users. Returns the problems (empty when ready)."""
    bad = []
    for argv in (["install", "-o", "munge", "-g", "munge", "-m", "0400", "/run/workbench/slurm/munge.key", "/etc/munge/munge.key"],
                 ["su", "-s", "/bin/sh", "munge", "-c", "munged --force"]):
        rc, out = run(argv)
        if rc != 0:
            bad.append(f"{argv[0]} failed: {out.strip()[-160:]}")
    for u in users:
        rc, out = run(["useradd", "-M", "-u", str(lab_uid(u)), "-s", "/bin/sh", u])
        if rc not in (0, 9):  # 9: already exists
            bad.append(f"useradd {u} failed: {out.strip()[-160:]}")
    return bad


class Replay:
    """One side's replay with an injectable runner, clock, sleep and flag check, so the tests need no Slurm."""

    def __init__(self, doc: dict, run: Runner = run_cmd, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
                 flag: Callable[[str], str | None] | None = None, say: Callable[[str], None] = print, deadline_s: float | None = None):
        self.jobs = sorted(doc["jobs"], key=lambda j: (j["submit_s"], j["key"]))
        self.users = sorted({j["user"] for j in self.jobs})
        self.length_s = float(doc.get("length_s") or 0)
        self.run, self.clock, self.sleep, self.say = run, clock, sleep, say
        self.flag = flag or _read_flag
        self.deadline_s = deadline_s if deadline_s is not None else 2 * self.length_s + 3600  # never runs forever
        self.ids: dict[str, str] = {}  # key -> Slurm job id
        self.rejected: dict[str, str] = {}  # key -> sbatch's message (first 160 characters)
        self.replay_s = self.paused_s = 0.0
        self.paused = False
        self.counts = {"running": 0, "pending": 0}

    def progress(self) -> dict:
        return {"submitted": len(self.ids), "rejected": len(self.rejected), "running": self.counts["running"], "pending": self.counts["pending"],
                "replay_s": round(self.replay_s, 1), "paused": self.paused, "paused_s": round(self.paused_s, 1), "jobs": len(self.jobs)}

    def poll(self) -> None:
        """Running and pending counts of this side's jobs, from squeue (as root: every user)."""
        if not self.ids:
            return
        rc, out = self.run(["squeue", "-h", "-o", "%j %T", "-u", ",".join(self.users)])
        if rc != 0:
            return
        keys = set(self.ids)
        states = [ln.split()[1] for ln in out.splitlines() if len(ln.split()) == 2 and ln.split()[0] in keys]
        self.counts = {"running": sum(s in ("RUNNING", "COMPLETING") for s in states), "pending": sum(s == "PENDING" for s in states)}

    def cancel(self) -> None:
        if self.ids:
            self.run(["scancel", *sorted(self.ids.values())])

    def submit(self, job: dict) -> None:
        rc, out = self.run(sbatch_argv(job))
        jid = out.strip().split(";")[0].strip() if rc == 0 else ""
        if rc == 0 and jid.isdigit():
            self.ids[job["key"]] = jid
        else:
            self.rejected[job["key"]] = out.strip()[-160:] or f"exit {rc}"

    def __call__(self) -> dict:
        """Replay until every job was submitted and has left the queue, the abort flag appears, or the deadline passes."""
        start = last = self.clock()
        nxt, said = 0, -PROGRESS_EVERY_S
        reason = None
        while True:
            now = self.clock()
            dt, last = now - last, now
            if (why := self.flag(ABORT_FLAG)) is not None:
                reason = why.strip() or "aborted"
                self.cancel()
                break
            self.paused = self.flag(PAUSE_FLAG) is not None
            if self.paused:
                self.paused_s += dt
            else:
                self.replay_s += dt
                while nxt < len(self.jobs) and self.jobs[nxt]["submit_s"] <= self.replay_s:
                    self.submit(self.jobs[nxt])
                    nxt += 1
            if now - said >= PROGRESS_EVERY_S:
                self.poll()
                self.say("@progress " + json.dumps(self.progress(), sort_keys=True))
                said = now
                if nxt >= len(self.jobs) and not self.counts["running"] and not self.counts["pending"]:
                    break
            if now - start > self.deadline_s:
                reason = f"the replay did not end within {int(self.deadline_s)} s"
                self.cancel()
                break
            self.sleep(1.0)
        self.poll()
        out = {**self.progress(), "reason": reason, "rejected_keys": sorted(self.rejected)[:50],
               "rejected_example": next(iter(self.rejected.values()), None)}
        self.say("@done " + json.dumps(out, sort_keys=True))
        return out


def _read_flag(name: str) -> str | None:
    """The flag's text ('' for an empty file), or None when it does not exist."""
    try:
        with open(os.path.join(FLAGS, name)) as f:
            return f.read(200)
    except FileNotFoundError:
        return None
    except OSError:
        return ""


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="lab_driver")
    ap.add_argument("--jobs", default="/run/lab/jobs.json")
    ap.add_argument("--deadline", type=float, default=None, help="seconds; default twice the run length plus an hour")
    a = ap.parse_args(argv)
    with open(a.jobs) as f:
        doc = json.load(f)
    rep = Replay(doc, say=lambda s: print(s, flush=True), deadline_s=a.deadline)
    if bad := setup(rep.users, run_cmd):
        print("@done " + json.dumps({**rep.progress(), "reason": "the driver could not start: " + "; ".join(bad[:3])}), flush=True)
        return 1
    return 0 if rep()["reason"] is None else 1


if __name__ == "__main__":
    sys.exit(main())
