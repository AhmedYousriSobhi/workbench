import ctypes
import os
import resource

import pytest

from governor import memlock, signals
from governor.signals import CgroupSampler, FileSampler, Sampler


def fake_proc(tmp_path, avail_kb=4_000_000, some=1.5, full=0.2, pswpout=100):
    (tmp_path / "pressure").mkdir(exist_ok=True)
    (tmp_path / "meminfo").write_text(f"MemTotal: 16000000 kB\nMemAvailable: {avail_kb} kB\n")
    (tmp_path / "pressure/memory").write_text(
        f"some avg10={some} avg60=0.00 avg300=0.00 total=1\nfull avg10={full} avg60=0.00 avg300=0.00 total=1\n")
    (tmp_path / "vmstat").write_text(f"pgfault 1\npswpout {pswpout}\n")
    return tmp_path


def test_sampler_reads_proc_and_derives_the_swap_out_rate(tmp_path):
    s = Sampler(fake_proc(tmp_path, pswpout=100))
    first = s.sample(10.0)
    assert first.mem_available == 4_000_000 * 1024
    assert (first.psi_some_avg10, first.psi_full_avg10, first.swap_out_rate) == (1.5, 0.2, 0.0)  # no history yet
    fake_proc(tmp_path, pswpout=300)
    assert s.sample(14.0).swap_out_rate == 50.0  # 200 pages in 4 s
    assert s.sample(14.0).swap_out_rate == 0.0  # same timestamp: no division by zero


def test_psi_without_a_full_line_reports_zero_full(tmp_path):
    proc = fake_proc(tmp_path)
    (proc / "pressure/memory").write_text("some avg10=3.00 avg60=0 avg300=0 total=1\n")
    assert Sampler(proc).sample(1.0).psi_full_avg10 == 0.0


def test_parse_pswpout_missing_is_zero():
    assert signals.parse_pswpout("pgfault 5\n") == 0


def test_file_sampler_reads_the_json(tmp_path):
    f = tmp_path / "s.json"
    f.write_text('{"mem_available": 1000, "psi_some": 2.5, "psi_full": 0.5}')
    s = FileSampler(f).sample(0)
    assert (s.mem_available, s.psi_some_avg10, s.psi_full_avg10) == (1000, 2.5, 0.5)
    f.write_text('{"mem_available": 5, "psi_some": 0, "psi_full": 0}')
    assert FileSampler(f).sample(0).mem_available == 5  # re-read every sample: tests can move the signal


def make_cgroup(tmp_path, cap, cur):
    (tmp_path / "memory.pressure").write_text("some avg10=9.0 avg60=0 avg300=0 total=1\nfull avg10=4.0 avg60=0 avg300=0 total=1\n")
    (tmp_path / "memory.max").write_text(f"{cap}\n")
    (tmp_path / "memory.current").write_text(f"{cur}\n")
    return tmp_path


def test_cgroup_sampler_headroom_and_pressure(tmp_path):
    s = CgroupSampler(make_cgroup(tmp_path, cap=1000, cur=400)).sample(0)
    assert (s.mem_available, s.psi_some_avg10, s.psi_full_avg10) == (600, 9.0, 4.0)
    assert CgroupSampler(make_cgroup(tmp_path, cap=1000, cur=1500)).sample(0).mem_available == 0  # over the cap: never negative
    assert CgroupSampler(make_cgroup(tmp_path, cap="max", cur=1500)).sample(0).mem_available == 1 << 40  # uncapped


def test_lock_memory_raises_with_the_errno_when_mlockall_fails(monkeypatch):
    class Libc:
        def mlockall(self, flags):
            assert flags == memlock.MCL_CURRENT | memlock.MCL_FUTURE
            ctypes.set_errno(12)
            return -1

    monkeypatch.setattr(memlock.ctypes, "CDLL", lambda *a, **k: Libc())
    with pytest.raises(OSError) as e:
        memlock.lock_memory()
    assert e.value.errno == 12


def test_lock_memory_succeeds_when_mlockall_returns_zero(monkeypatch):
    class Libc:
        def mlockall(self, flags):
            return 0

    monkeypatch.setattr(memlock.ctypes, "CDLL", lambda *a, **k: Libc())
    memlock.lock_memory()


def test_locked_kb_reads_vmlck(monkeypatch, tmp_path):
    status = tmp_path / "status"
    status.write_text("Name: x\nVmLck:\t  2048 kB\n")
    real = memlock.Path
    monkeypatch.setattr(memlock, "Path", lambda p: status if p == "/proc/self/status" else real(p))
    assert memlock.locked_kb() == 2048
    status.write_text("Name: x\n")
    assert memlock.locked_kb() == 0


def test_raise_memlock_limit_lifts_soft_to_hard(monkeypatch):
    calls = []
    monkeypatch.setattr(memlock.resource, "getrlimit", lambda r: (100, 5000))
    monkeypatch.setattr(memlock.resource, "setrlimit", lambda r, v: calls.append((r, v)))
    memlock.raise_memlock_limit()
    assert calls == [(resource.RLIMIT_MEMLOCK, (5000, 5000))]


def test_mitigation_path_writes_through_preopened_handles(tmp_path):
    f = tmp_path / "cg.file"
    f.write_bytes(b"")
    p = memlock.MitigationPath(buf_size=4096)
    assert len(p.buf) == 4096 and not p.has(str(f))
    fd = p.open(str(f), os.O_WRONLY)
    assert p.open(str(f)) == fd and p.has(str(f))  # reopening returns the same descriptor, no new open
    p.write(str(f), b"1")
    assert f.read_bytes() == b"1"
    p.forget(str(tmp_path))
    assert not p.has(str(f))
    p.open(str(f))
    p.close()
    assert not p.has(str(f))
