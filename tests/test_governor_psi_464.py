"""PSI trigger, pre-opened signal files and thresholds derived from sim.yaml (issue 464, ADR 0059)."""
import builtins
import os
from pathlib import Path

import pytest

from governor import psi
from governor.hostsize import DEFAULT, derive, read_host
from governor.ladder import Thresholds
from governor.signals import GB, Sampler

ROOT = Path(__file__).resolve().parents[1]


def proc_tree(tmp_path):
    proc = tmp_path / "proc"
    (proc / "pressure").mkdir(parents=True)
    (proc / "pressure/memory").write_text("some avg10=2.00 avg60=0 avg300=0 total=0\nfull avg10=1.00 avg60=0 avg300=0 total=0\n")
    (proc / "meminfo").write_text("MemAvailable:    2048 kB\n")
    (proc / "vmstat").write_text("pswpout 10\n")
    return proc


def test_the_signal_path_opens_nothing_after_construction(tmp_path, monkeypatch):
    s = Sampler(proc_tree(tmp_path), tmp_path / "none")
    s.sample(0)

    def boom(*a, **k):
        raise AssertionError("opened a file on the signal path")

    monkeypatch.setattr(builtins, "open", boom)
    monkeypatch.setattr(os, "open", boom)
    monkeypatch.setattr(Path, "read_text", boom)
    monkeypatch.setattr(Path, "glob", boom)
    bufs = [id(r.buf) for r in (s._meminfo, s._psi, s._vmstat)]
    for t in range(1, 30):  # stays under ZRAM_RESCAN, the one place that looks for devices
        got = s.sample(t)
    assert got.mem_available == 2048 * 1024 and got.psi_some_avg10 == 2.0
    assert bufs == [id(r.buf) for r in (s._meminfo, s._psi, s._vmstat)]  # the same buffers, reused


def test_a_zram_device_that_appears_later_is_found_on_the_next_rescan(tmp_path):
    blk = tmp_path / "b"
    s = Sampler(proc_tree(tmp_path), blk)
    assert s.sample(0).compressed_swap == 0
    (blk / "zram0").mkdir(parents=True)
    (blk / "zram0/mm_stat").write_text("1 1 777 0 0 0 0 0\n")
    for t in range(1, Sampler.ZRAM_RESCAN):
        s.sample(t)
    assert s.sample(Sampler.ZRAM_RESCAN).compressed_swap == 777


def test_the_trigger_is_inactive_and_just_sleeps_when_the_kernel_refuses(tmp_path):
    t = psi.PsiTrigger(str(tmp_path / "missing"))
    assert not t.active and "cannot open" in t.why
    assert t.wait(0.01) is False
    f = tmp_path / "ro"
    f.write_text("")
    os.chmod(f, 0o444)
    r = psi.PsiTrigger(str(f))
    assert not r.active and r.why


def test_the_trigger_registers_with_the_kernel_spec_and_reports_an_event(tmp_path):
    f = tmp_path / "memory"
    f.write_text("")
    t = psi.PsiTrigger(str(f))  # a plain file accepts the write: active, and the spec is what we meant to send
    assert t.active and f.read_text().startswith("some 150000 2000000")  # 150 ms in a 2 s window (unprivileged windows are multiples of 2 s)
    class Fake:
        def __init__(self, out):
            self.out = out

        def poll(self, ms):
            return self.out

    t._poll = Fake([(t.fd, 2)])
    assert t.wait(5) is True
    t._poll = Fake([])
    assert t.wait(0.01) is False
    t.close()
    assert not t.active


def test_the_real_kernel_accepts_the_trigger_as_a_normal_user_or_says_why():
    if not Path(psi.PATH).exists():
        pytest.skip("no PSI on this kernel")
    t = psi.PsiTrigger()
    assert t.active or t.why  # both outcomes are handled; the log says which
    t.close()


def test_the_shipped_sim_yaml_derives_the_old_readme_thresholds():
    assert read_host(ROOT / "sim.yaml") == (3.0, 1.0)
    d = derive(3.0, 1.0)
    t = Thresholds()
    assert (d["l4_mem"], d["l1_mem"], d["l0_mem"]) == (t.l4_mem, t.l1_mem, t.l0_mem)


def test_a_bigger_reserve_moves_all_three_and_the_order_always_holds():
    d = derive(6.0, 2.0)
    assert d == {"l4_mem": 2 * GB, "l1_mem": 4 * GB, "l0_mem": 5 * GB}
    assert derive(1.0, 1.0)["l0_mem"] == derive(1.0, 1.0)["l4_mem"]  # a reserve at the floor leaves no room, never a negative one
    assert derive(0.5, 1.0)["l1_mem"] >= derive(0.5, 1.0)["l4_mem"]


def test_missing_file_or_keys_fall_back_to_the_defaults(tmp_path):
    assert read_host(tmp_path / "none") == DEFAULT
    f = tmp_path / "sim.yaml"
    f.write_text("host:\n  reserve_gb: 4.5\nother:\n  safety_floor_gb: 9\n")
    assert read_host(f) == (4.5, DEFAULT[1])  # only the host block counts
    f.write_text("nothing: 1\n")
    assert read_host(f) == DEFAULT


def test_admit_uses_the_floor_it_is_given():
    from governor.core import Governor
    from governor.ladder import Ladder
    from governor.signals import FileSampler
    g = Governor(FileSampler(Path("/x")), Ladder(), [], lambda a: None, True, admit_floor=3 * GB)
    g.signals = type("S", (), {"mem_available": 4 * GB})()
    assert "floor 3.0" in g._admit(2048)["error"]
