#!/usr/bin/env python3
"""Simulated users submitting jobs, so the cluster has a live queue (issue 153). Runs on the login node: `benchctl load on`.

Arrivals are a Poisson process (exponential gaps, mean 60/rate s). Each job is a `sleep` in the cpu partition, or the gpu
partition holding 1 to 4 GPUs, run as one of the simulated users. It stops adding work while the queue is long."""
import argparse
import random
import subprocess
import sys
import time

MAX_PENDING = 20


def run(*cmd: str) -> str:
    r = subprocess.run(cmd, check=False, capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def pick(rng: random.Random, users: list[str], gpus_per_node: int, mean_s: float) -> list[str]:
    """The runuser/sbatch command for one arriving job."""
    user = rng.choice(users)
    gpu = gpus_per_node > 0 and rng.random() < 0.25
    seconds = max(10, int(rng.expovariate(1 / mean_s)))
    args = ["sbatch", "--parsable", f"--partition={'gpu' if gpu else 'cpu'}", f"--job-name={user}-{'train' if gpu else 'batch'}",
            f"--time={seconds // 60 + 2}", "--output=/dev/null", "--error=/dev/null", f"--cpus-per-task={rng.choice([1, 2, 4])}", *([f"--gres=gpu:{rng.randint(1, min(4, gpus_per_node))}"] if gpu else []),
            f"--wrap=sleep {seconds}"]
    return ["runuser", "-u", user, "--", *args]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--rate", type=float, default=6, help="jobs per minute")
    ap.add_argument("--duration", type=float, default=120, help="mean job length in seconds")
    ap.add_argument("--users", default="alice,bob")
    ap.add_argument("--gpus", type=int, default=8, help="GPUs per GPU node (0: cpu jobs only)")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()
    rng, users = random.Random(a.seed), a.users.split(",")
    while True:
        time.sleep(rng.expovariate(a.rate / 60))
        try:
            if int(run("sh", "-c", "squeue -h -t PD | wc -l") or 0) < MAX_PENDING:
                run(*pick(rng, users, a.gpus, a.duration))
        except (subprocess.SubprocessError, ValueError):
            pass  # slurmctld busy or down: try again at the next arrival


if __name__ == "__main__":
    sys.exit(main())
