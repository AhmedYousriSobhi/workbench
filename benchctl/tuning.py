"""Host tuning for the memory budget (SPEC P-3): zram swap, swappiness, THP.

Print-only (SPEC NF-5): this module reads /proc and /sys and returns text. It never runs sudo and never changes the host.
"""
from dataclasses import dataclass
from pathlib import Path

SWAPPINESS = 10  # "low-ish" (docs/architecture/05-governor.md): keep page cache and anon pages in RAM, swap only under pressure
ZRAM_PERCENT = 25  # low end of "about 25-50% of RAM"
THP = "madvise"


@dataclass(frozen=True)
class Tuning:
    setting: str
    current: str
    wanted: str
    applied: bool
    command: str
    effect: str
    undo: str


def _read(path: str) -> str | None:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return None


def _thp(raw: str | None) -> str:
    """`always [madvise] never` -> `madvise`."""
    if not raw:
        return "unknown"
    for word in raw.split():
        if word.startswith("[") and word.endswith("]"):
            return word[1:-1]
    return raw


def _mem_total_kb(meminfo: str) -> int:
    for ln in meminfo.splitlines():
        if ln.startswith("MemTotal:"):
            return int(ln.split()[1])
    return 0


def plan(meminfo: str | None = None, zram: bool | None = None, swappiness: str | None = None, thp: str | None = None) -> list[Tuning]:
    """One entry per setting. Readers are injectable so tests need no host."""
    text = meminfo if meminfo is not None else (_read("/proc/meminfo") or "")
    have_zram = zram if zram is not None else Path("/sys/block/zram0").exists()
    swp = swappiness if swappiness is not None else _read("/proc/sys/vm/swappiness")
    cur_thp = _thp(thp if thp is not None else _read("/sys/kernel/mm/transparent_hugepage/enabled"))
    mb = max(_mem_total_kb(text) * ZRAM_PERCENT // 100 // 1024, 1024)
    return [
        Tuning("zram", "present" if have_zram else "absent", f"zstd zram swap, {mb} MiB ({ZRAM_PERCENT}% of RAM)", have_zram,
               f"sudo modprobe zram && dev=$(sudo zramctl --find --algorithm zstd --size {mb}M) && sudo mkswap \"$dev\" && sudo swapon --priority 100 \"$dev\"",
               "adds compressed swap in RAM, so memory.reclaim and the governor's L3 have somewhere to push pages; lost at reboot",
               "sudo swapoff /dev/zram0 && sudo zramctl --reset /dev/zram0"),
        Tuning("swappiness", swp or "unknown", str(SWAPPINESS), swp == str(SWAPPINESS),
               f"sudo sysctl vm.swappiness={SWAPPINESS}",
               "makes the kernel prefer dropping page cache over swapping anonymous pages; lost at reboot",
               f"sudo sysctl vm.swappiness={swp or 60}"),
        Tuning("thp", cur_thp, THP, cur_thp == THP,
               f"echo {THP} | sudo tee /sys/kernel/mm/transparent_hugepage/enabled",
               "huge pages only where a program asks for them, so databases and JVMs in the containers do not bloat resident memory; lost at reboot",
               f"echo {cur_thp if cur_thp not in ('unknown', THP) else 'always'} | sudo tee /sys/kernel/mm/transparent_hugepage/enabled"),
    ]


def render(items: list[Tuning]) -> str:
    """Text blocks for the settings that are not yet as recommended."""
    todo = [t for t in items if not t.applied]
    if not todo:
        return ""
    out = ["", "Host tuning (SPEC P-3). Printed only: doctor never runs sudo or changes the host. Run a block yourself if you want it."]
    for t in todo:
        out += ["", f"{t.setting}: now {t.current}, recommended {t.wanted}",
                f"  change: {t.command}", f"  effect: {t.effect}", f"  undo:   {t.undo}"]
    return "\n".join(out)


def as_dict(t: Tuning) -> dict:
    return {"setting": t.setting, "current": t.current, "wanted": t.wanted, "applied": t.applied,
            "command": t.command, "effect": t.effect, "undo": t.undo}
