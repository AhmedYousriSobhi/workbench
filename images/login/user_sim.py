#!/usr/bin/env python3
"""Simulated users submitting jobs, so the cluster has a live queue (issue 153). Runs on the login node: `benchctl load on`.

Arrivals are a Poisson process (exponential gaps, mean 60/rate s). Each job is a `sleep` in the cpu partition, or the gpu
partition holding 1 to 4 GPUs, run as one of the simulated users. It stops adding work while the queue is long.

Optional (issue 680, both off by default, same process and the same pending cap): `--personas` gives each user the job mix of the
persona in `sim.yaml` (`alice:ml-trainer`), with some jobs that fail or are cancelled; `--day SECONDS` runs a daily rhythm over a
simulated day that lasts SECONDS of real time, so the arrival rate rises and falls and you can see it quickly."""
import argparse
import math
import os
import random
import subprocess
import sys
import time

MAX_PENDING = 20
MAX_CANCELS = 2 * MAX_PENDING  # jobs waiting to be cancelled: bounded like the queue
RHYTHM_FLOOR = 0.1  # the slowest the night gets, as a share of --rate
# persona -> job mix. gpu: share of jobs on the gpu partition, gpus: (min, max) per GPU job, cpus: choices, length: x mean job
# length, weight: share of the arrivals, peak: hour of the day it is busiest, fail/cancel: share of jobs that exit non-zero / are scancelled
PERSONAS: dict[str, dict] = {
    "ml-trainer": {"gpu": 0.85, "gpus": (1, 4), "cpus": [4, 8], "length": 3.0, "weight": 1.0, "peak": 22, "fail": 0.10, "cancel": 0.10, "name": "train"},
    "mpi-researcher": {"gpu": 0.05, "gpus": (1, 1), "cpus": [4, 8, 16], "length": 1.5, "weight": 1.0, "peak": 14, "fail": 0.08, "cancel": 0.05, "name": "mpi"},
}
DEFAULT_PERSONA = {"gpu": 0.25, "gpus": (1, 4), "cpus": [1, 2, 4], "length": 1.0, "weight": 1.0, "peak": 14, "fail": 0.0, "cancel": 0.0, "name": ""}
PAUSED = "/run/workbench-flags/arrivals.paused"  # the governor's L1 flag, mounted read-only (issue 476)


def run(*cmd: str) -> str:
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def rhythm(hour: float, peak: float = 14, floor: float = RHYTHM_FLOOR) -> float:
    """Arrival-rate multiplier at `hour` (0 to 24): 1 + 0.9 cos around the busiest hour, mean 1, never below `floor`."""
    return max(floor, 1 + 0.9 * math.cos(2 * math.pi * (hour - peak) / 24))


def sim_hour(elapsed: float, day_s: float, start: float = 8) -> float:
    """Hour of the simulated day after `elapsed` real seconds when a day lasts `day_s` (0: no rhythm, always `start`)."""
    return (start + elapsed / day_s * 24) % 24 if day_s > 0 else start


def parse_users(spec: str) -> dict[str, str]:
    """`alice:ml-trainer,bob` -> {alice: ml-trainer, bob: ''}"""
    return {u.partition(":")[0]: u.partition(":")[2] for u in spec.split(",") if u}


def plan(rng: random.Random, users: dict[str, str], gpus_per_node: int, mean_s: float, hour: float | None = None,
         personas: bool = False) -> tuple[list[str], float | None]:
    """One arriving job: the runuser/sbatch command and, when this job will be cancelled, after how many seconds.
    Without `personas` every user gets the default mix and no job fails (the pre-#680 behaviour). With them each user gets the
    persona's mix, a share of jobs exit 1 (failed) or are scancelled, and users are chosen by weight x rhythm at `hour`."""
    names = list(users)
    prof = lambda u: (PERSONAS.get(users[u], DEFAULT_PERSONA) if personas else DEFAULT_PERSONA)
    h = 14 if hour is None else hour
    user = rng.choices(names, [prof(u)["weight"] * rhythm(h, prof(u)["peak"]) for u in names])[0] if personas and hour is not None else rng.choice(names)
    p = prof(user)
    gpu = gpus_per_node > 0 and rng.random() < p["gpu"]
    seconds = max(10, int(rng.expovariate(1 / (mean_s * p["length"]))))
    roll = rng.random() if personas else 1.0
    fails, cancelled = roll < p["fail"], p["fail"] <= roll < p["fail"] + p["cancel"]
    label = p["name"] or ("train" if gpu else "batch")
    wrap = f"sleep {max(5, seconds // 4)}; exit 1" if fails else f"sleep {seconds}"  # a failing job dies early with exit code 1
    args = ["sbatch", "--parsable", f"--partition={'gpu' if gpu else 'cpu'}", f"--job-name={user}-{label}",
            f"--time={seconds // 60 + 2}", "--output=/dev/null", "--error=/dev/null", f"--cpus-per-task={rng.choice(p['cpus'])}",
            *([f"--gres=gpu:{rng.randint(p['gpus'][0], min(p['gpus'][1], gpus_per_node))}"] if gpu else []),
            f"--wrap={wrap}"]
    return ["runuser", "-u", user, "--", *args], (rng.uniform(5, max(6, seconds / 2)) if cancelled else None)


def pick(rng: random.Random, users: list[str], gpus_per_node: int, mean_s: float) -> list[str]:
    """The runuser/sbatch command for one arriving job (default mix, no failures)."""
    return plan(rng, dict.fromkeys(users, ""), gpus_per_node, mean_s)[0]


def wait_for(gap: float, cancels: list[tuple[float, str, str]]) -> None:
    """Sleep `gap` seconds; while jobs wait to be cancelled, wake every second to scancel the ones that are due."""
    if not cancels:
        time.sleep(gap)
        return
    end = time.monotonic() + gap
    while (now := time.monotonic()) < end:
        for due in [c for c in cancels if c[0] <= now]:
            cancels.remove(due)
            try:
                run("runuser", "-u", due[1], "--", "scancel", due[2])
            except subprocess.SubprocessError:
                pass
        time.sleep(min(1.0, max(0.05, end - now)))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=6, help="jobs per minute")
    ap.add_argument("--duration", type=float, default=120, help="mean job length in seconds")
    ap.add_argument("--users", default="alice,bob", help="name or name:persona, comma separated")
    ap.add_argument("--gpus", type=int, default=8, help="GPUs per GPU node (0: cpu jobs only)")
    ap.add_argument("--personas", action="store_true", help="persona job mixes, failing and cancelled jobs")
    ap.add_argument("--day", type=float, default=0, help="real seconds one simulated day lasts (0: no daily rhythm)")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rng, users = random.Random(a.seed), parse_users(a.users)
    start = time.monotonic()
    cancels: list[tuple[float, str, str]] = []  # (when, user, job id)
    while True:
        hour = sim_hour(time.monotonic() - start, a.day) if a.day > 0 else None
        wait_for(rng.expovariate(a.rate * (rhythm(hour) if hour is not None else 1) / 60), cancels)
        if os.path.exists(PAUSED):
            continue  # L1: no new work until the governor de-escalates
        try:
            if int(run("sh", "-c", "squeue -h -t PD | wc -l") or 0) < MAX_PENDING:
                cmd, cancel_s = plan(rng, users, a.gpus, a.duration, hour, a.personas)
                jid = run(*cmd).split(";")[0]
                if cancel_s is not None and jid.isdigit() and len(cancels) < MAX_CANCELS:
                    cancels.append((time.monotonic() + cancel_s, cmd[2], jid))
        except (subprocess.SubprocessError, ValueError):
            pass  # slurmctld busy or down: try again at the next arrival


if __name__ == "__main__":
    sys.exit(main())
