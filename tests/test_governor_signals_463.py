"""Swap storm, memory.events and compressed swap (issue 463, GV-2)."""
import json

from governor.core import EVENTS_EVERY, Governor
from governor.ladder import Ladder, Thresholds
from governor.signals import GB, FileSampler, Sampler, Signals, parse_memory_events, zram_bytes


def sig(swap=0.0, avail=8 * GB, psi=0.0):
    return Signals(avail, psi, 0.0, swap)


def test_a_short_swap_burst_does_nothing_and_a_sustained_one_adds_a_level():
    lad = Ladder(Thresholds(swap_pages_s=100, swap_sustain_s=10))
    assert lad.update(sig(500), 0) == 0
    assert lad.update(sig(500), 9) == 0
    assert lad.update(sig(500), 10) == 2  # calm base 0 counts as L1, plus one
    assert lad.update(sig(0), 11) == 2  # steps down only after calm_s, like any level


def test_a_swap_storm_adds_one_to_the_pressure_level_but_never_passes_l3_or_touches_l4():
    lad = Ladder(Thresholds(swap_pages_s=100, swap_sustain_s=0, sustain_s=0))
    assert lad.target(sig(500, psi=25), 0) == 3  # PSI says L2, swap adds one
    assert lad.target(sig(500, psi=35), 1) == 3  # L3 stays L3
    assert lad.target(sig(500, avail=int(0.5 * GB)), 2) == 4  # L4 is decided by headroom alone


def test_swap_threshold_zero_turns_the_trigger_off():
    lad = Ladder(Thresholds(swap_pages_s=0, swap_sustain_s=0))
    assert lad.target(sig(1e9), 0) == 0


def test_sampler_reads_the_swap_rate_and_takes_compressed_pools_off_the_headroom(tmp_path):
    proc, blk = tmp_path / "proc", tmp_path / "block"
    (proc / "pressure").mkdir(parents=True)
    (blk / "zram0").mkdir(parents=True)
    (proc / "pressure/memory").write_text("some avg10=1.00 avg60=0 avg300=0 total=0\nfull avg10=0.00 avg60=0 avg300=0 total=0\n")
    (blk / "zram0/mm_stat").write_text("100 50 300 0 400 0 0 0\n")
    (proc / "meminfo").write_text("MemAvailable:    1000 kB\nZswap:    200 kB\n")
    (proc / "vmstat").write_text("pswpout 0\n")
    s = Sampler(proc, blk)
    s.sample(0)
    (proc / "vmstat").write_text("pswpout 500\n")
    got = s.sample(5)
    assert got.swap_out_rate == 100 and got.compressed_swap == 200 * 1024 + 300
    assert got.mem_available == 1000 * 1024 - got.compressed_swap
    (proc / "meminfo").write_text("MemAvailable:    1 kB\nZswap:    200 kB\n")
    assert s.sample(6).mem_available == 0  # never negative


def test_zram_bytes_ignores_a_broken_device(tmp_path):
    (tmp_path / "zram0").mkdir()
    (tmp_path / "zram0/mm_stat").write_text("garbage")
    assert zram_bytes(tmp_path) == 0 and zram_bytes(tmp_path / "none") == 0


def test_the_signals_file_can_drive_swap_and_pools(tmp_path):
    f = tmp_path / "s.json"
    f.write_text(json.dumps({"mem_available": 5, "psi_some": 1, "psi_full": 0, "swap_out_rate": 9.5, "compressed_swap": 7}))
    s = FileSampler(f).sample(0)
    assert (s.swap_out_rate, s.compressed_swap) == (9.5, 7)


def test_parse_memory_events():
    assert parse_memory_events("low 0\nhigh 4\nmax 1\noom 2\noom_kill 1\n") == {"low": 0, "high": 4, "max": 1, "oom": 2, "oom_kill": 1}


class Ex:
    def __init__(self, cgroups):
        self.cgroups = cgroups

    def __call__(self, a):
        pass


def gov(tmp_path, f):
    cg = tmp_path / "cn1"
    cg.mkdir()
    ev = cg / "memory.events"
    ev.write_text("high 0\noom 0\noom_kill 0\n")
    sig_f = tmp_path / "sig.json"
    sig_f.write_text(json.dumps({"mem_available": 8 * GB, "psi_some": 0, "psi_full": 0}))
    return Governor(FileSampler(sig_f), Ladder(), [], Ex({"cn1": cg}), True), ev


def test_oom_kill_is_an_audit_event_and_high_shows_in_status_and_metrics(tmp_path):
    g, ev = gov(tmp_path, None)
    g.step(0)  # first read: the baseline, no event for what was there before
    assert not any("OOM" in a for a in g.audit)
    ev.write_text("high 7\noom 1\noom_kill 1\n")
    g.step(EVENTS_EVERY + 1)
    assert any("OOM KILL: cn1 (+1)" in a for a in g.audit)
    st = g.handle("status", {})
    assert st["memory_events"] == {"high": 7, "oom": 1, "oom_kill": 1} and st["memory_high_top"] == [("cn1", 7)]
    assert 'workbench_container_memory_events{name="cn1",event="high"} 7' in g.metrics.decode()
    g.step(2 * EVENTS_EVERY + 2)  # no new kill: no new event
    assert sum("OOM KILL" in a for a in g.audit) == 1


def test_events_are_read_only_every_few_seconds_and_not_from_l3(tmp_path):
    g, ev = gov(tmp_path, None)
    g.step(0)
    ev.write_text("high 1\noom 0\noom_kill 0\n")
    g.step(1)
    assert g.events["cn1"]["high"] == 0  # too soon
    g.ladder.level = 3
    g._read_events(100, 3)
    assert g.events["cn1"]["high"] == 0  # not from L3 up (GV-8)


def test_a_vanished_cgroup_is_skipped(tmp_path):
    g, ev = gov(tmp_path, None)
    ev.unlink()
    g.step(0)
    assert g.events == {}
