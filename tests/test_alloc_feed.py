import importlib.util
import random
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


feed = load("images/slurm-ctl/alloc_feed.py", "alloc_feed")
exporter = load("components/gpu-exporter-farm/exporter.py", "exporter_alloc")

RUNNING = ("JobId=7 JobName=wrap UserId=alice(1001) JobState=RUNNING Reason=None "
           "NumNodes=2 TRES=cpu=2 JOB_GRES=gpu:4 Nodes=gpu001 CPU_IDs=0-1 Mem=0 GRES=gpu(IDX:0-1) "
           "Nodes=gpu002 CPU_IDs=0 Mem=0 GRES=gpu:h200(IDX:2,4-5) StdOut=/x")
PENDING = "JobId=8 JobState=PENDING Reason=Resources JOB_GRES=gpu:8"
CPU_ONLY = "JobId=9 JobState=RUNNING Nodes=cn001 CPU_IDs=0 Mem=0 GRES=(null)"


def test_parse_running_gpu_jobs_per_node():
    got = feed.parse_jobs(f"{RUNNING}\n{PENDING}\n{CPU_ONLY}")
    assert got == {"gpu001": [0, 1], "gpu002": [2, 4, 5]}


def test_node_and_index_ranges():
    assert feed.expand_nodes("gpu[001-002,004],cn003") == ["gpu001", "gpu002", "gpu004", "cn003"]
    assert feed.indexes("0-2,5") == [0, 1, 2, 5]


def sample(state, metric):
    out = {}
    for line in state.render().splitlines():
        if line.startswith(metric + "{"):
            labels, value = line.rsplit(" ", 1)
            out[int(labels.split('gpu="')[1].split('"')[0])] = float(value)
    return out


def test_exporter_shows_allocated_gpus_busy_and_the_rest_idle():
    s = exporter.State({"gpu001": 4}, random.Random(3), "h200")
    idle = sample(s, "DCGM_FI_DEV_GPU_UTIL")
    assert max(idle.values()) <= 3
    s.set_alloc({"gpu001": [0, 1]})
    util = sample(s, "DCGM_FI_DEV_GPU_UTIL")
    assert util[0] >= 70 and util[1] >= 70 and util[2] <= 3 and util[3] <= 3
    used, power = sample(s, "DCGM_FI_DEV_FB_USED"), sample(s, "DCGM_FI_DEV_POWER_USAGE")
    assert used[0] > s.fb_total * 0.5 > used[2] and power[0] > 400 > power[2]
    s.set_alloc({})  # job ended
    assert max(sample(s, "DCGM_FI_DEV_GPU_UTIL").values()) <= 3


def test_follow_slurm_keeps_the_last_answer_then_idles(monkeypatch):
    s = exporter.State({"gpu001": 2}, random.Random(1))
    stop = threading.Event()
    answers = [{"gpu001": [1]}, OSError("down"), OSError("down")]

    def fetch():
        a = answers.pop(0)
        if not answers:
            stop.set()
        if isinstance(a, Exception):
            raise a
        return a

    t = iter([0.0, 0.0, 1.0, 100.0, 100.0, 100.0])
    monkeypatch.setattr(exporter.time, "monotonic", lambda: next(t, 100.0))
    exporter.follow_slurm(s, fetch, interval=0, stop=stop)
    assert s.alloc == {}  # unreachable for longer than ALLOC_STALE_S


def test_follow_slurm_holds_allocation_during_a_short_outage(monkeypatch):
    s = exporter.State({"gpu001": 2}, random.Random(1))
    stop = threading.Event()
    calls = iter([{"gpu001": [1]}, OSError("blip")])

    def fetch():
        a = next(calls)
        if isinstance(a, Exception):
            stop.set()
            raise a
        return a

    exporter.follow_slurm(s, fetch, interval=0, stop=stop)
    assert s.alloc == {"gpu001": {1}}
