#!/usr/bin/env python3
"""`df` and `lfs` for the declared storage (issue 461, ADR 0078). Installed as /usr/local/bin/df and /usr/local/bin/lfs.

The mounts, their size and fill level, and which of them this node cannot reach come from the GPU exporter farm (GET /sim/storage?node=),
the same numbers its `node_filesystem_*` series carry, so a scenario that fills a mount changes both. Output follows GNU `df` and Lustre's
`lfs df` and `lfs quota` in layout; it is a simulation of the shape, not of every flag. A path that is not a declared mount, or an exporter
that does not answer, gets the real `df`. Standard library only.
"""
import getpass
import json
import math
import os
import pwd
import socket
import subprocess
import sys
import urllib.request

URL = os.environ.get("WORKBENCH_STORAGE_URL", "http://gpu-exporter-farm:9400/sim/storage")
REAL_DF = "/usr/bin/df"
STALE = {"nfs4": "Stale file handle", "lustre": "Cannot send after transport endpoint shutdown", "ext4": "Input/output error"}


def view() -> list[dict] | None:
    try:
        with urllib.request.urlopen(f"{URL}?node={socket.gethostname()}", timeout=1) as r:
            return json.load(r)["mounts"]
    except (OSError, ValueError, KeyError):
        return None


def human(n: float, base: int = 1024) -> str:
    """GNU `df -h`: rounded up, one decimal below 10."""
    for unit in ("", "K", "M", "G", "T", "P", "E"):
        if n < base or unit == "E":
            return f"{math.ceil(n * 10) / 10:g}{unit}" if n < 10 and unit else f"{math.ceil(n)}{unit}"
        n /= base
    return str(n)


def pct(used: float, total: float) -> str:
    return f"{math.ceil(used * 100 / total)}%" if total else "-"


def table(rows: list[list[str]]) -> str:
    """Left-align the first column, right-align the rest but the last (GNU df layout)."""
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for r in rows:
        cells = [r[0].ljust(widths[0])] + [c.rjust(widths[i]) for i, c in enumerate(r[1:-1], 1)] + [r[-1]]
        out.append(" ".join(cells))
    return "\n".join(out) + "\n"


def cover(mounts: list[dict], path: str) -> dict | None:
    real = os.path.realpath(path)
    hits = [m for m in mounts if real == m["path"] or real.startswith(m["path"].rstrip("/") + "/")]
    return max(hits, key=lambda m: len(m["path"])) if hits else None


def df(argv: list[str]) -> int:
    flags = {a for a in argv if a.startswith("-") and not a.startswith("--")}
    short = "".join(f[1:] for f in flags)
    longs = {a for a in argv if a.startswith("--")}
    paths = [a for a in argv if not a.startswith("-")]
    known = set("hHkiTP")
    mounts = view()
    if mounts is None or set(short) - known or longs - {"--human-readable", "--inodes", "--print-type", "--si"}:
        os.execv(REAL_DF, [REAL_DF, *argv])
        return 0  # not reached: execv replaces this process
    human_ = "h" in short or "--human-readable" in longs
    si = "H" in short or "--si" in longs
    inodes = "i" in short or "--inodes" in longs
    typed = "T" in short or "--print-type" in longs
    base = 1000 if si else 1024
    fmt = (lambda n: human(n, base)) if (human_ or si) else (lambda n: str(int(n // 1024)))
    fmt_i = (lambda n: human(n, base)) if (human_ or si) else (lambda n: str(int(n)))
    head = ["Filesystem"] + (["Type"] if typed else []) + (["Inodes", "IUsed", "IFree", "IUse%"] if inodes else (["Size", "Used", "Avail", "Use%"] if human_ or si else ["1K-blocks", "Used", "Available", "Use%"])) + ["Mounted on"]
    rows, code = [], 0

    def fake(m: dict) -> list[str]:
        if inodes:
            nums = [fmt_i(m["files"]), fmt_i(m["files_used"]), fmt_i(m["files_free"]), pct(m["files_used"], m["files"])]
        else:
            nums = [fmt(m["size"]), fmt(m["used"]), fmt(m["free"]), pct(m["used"], m["size"])]
        return [m["source"]] + ([m["fstype"]] if typed else []) + nums + [m["path"]]

    def real(extra: list[str]) -> list[list[str]]:
        r = subprocess.run([REAL_DF, *[f for f in argv if f.startswith("-")], *extra], capture_output=True, text=True, check=False)
        lines = r.stdout.splitlines()[1:]
        return [ln.split(None, len(head) - 1) for ln in lines]

    def stale(m: dict, shown: str) -> None:
        nonlocal code
        sys.stderr.write(f"df: {shown}: {STALE.get(m['fstype'], 'Input/output error')}\n")
        code = 1

    if paths:
        plain = []
        for p in paths:
            m = cover(mounts, p)
            if m is None:
                plain.append(p)
            elif m["stale"]:
                stale(m, p)
            else:
                rows.append(fake(m))
        rows += real(plain) if plain else []
    else:
        rows += real([])
        rows += [fake(m) for m in mounts if not m["stale"]]
        for m in mounts:
            if m["stale"]:
                stale(m, m["path"])
    if rows:
        sys.stdout.write(table([head, *rows]))
    return code


def lfs_mounts(mounts: list[dict], path: str | None) -> list[dict] | None:
    lus = [m for m in mounts if m["fstype"] == "lustre"]
    if path is None:
        return lus
    m = cover(lus, path)
    return [m] if m else None


def lfs_df(argv: list[str], mounts: list[dict]) -> int:
    human_, inodes = "-h" in argv, "-i" in argv
    paths = [a for a in argv if not a.startswith("-")]
    chosen = lfs_mounts(mounts, paths[0] if paths else None)
    if not chosen:
        sys.stderr.write(f"error: llapi_search_mounts: {paths[0]!r} is not on a Lustre filesystem\n" if paths else "lfs df: no Lustre filesystem is mounted\n")
        return 1
    fmt = human if human_ else (lambda n: str(int(n // 1024)))
    rows, code = [["UUID"] + (["Inodes", "IUsed", "IFree", "IUse%"] if inodes else (["bytes", "Used", "Available", "Use%"] if human_ else ["1K-blocks", "Used", "Available", "Use%"])) + ["Mounted on"]], 0
    for m in chosen:
        fs = os.path.basename(m["path"])
        if m["stale"]:
            sys.stderr.write(f"lfs df: cannot get mount info for {m['path']}: {STALE['lustre']}\n")
            code = 1
            continue
        osts = 4
        def line(name: str, tag: str, size: float, used: float, files: int, files_used: int, where: str = m["path"]) -> list[str]:
            nums = ([str(int(files)), str(int(files_used)), str(int(files - files_used)), pct(files_used, files)] if inodes
                    else [fmt(size), fmt(used), fmt(size - used), pct(used, size)])
            return [name] + nums + [f"{where}{tag}"]
        rows.append(line(f"{fs}-MDT0000_UUID", "[MDT:0]", 2 * 1024**4, 2 * 1024**4 * m["used"] / m["size"], m["files"], m["files_used"]))
        for i in range(osts):
            rows.append(line(f"{fs}-OST{i:04x}_UUID", f"[OST:{i}]", m["size"] / osts, m["used"] / osts, m["files"] // osts, m["files_used"] // osts))
        rows.append(line("filesystem_summary:", "", m["size"], m["used"], m["files"], m["files_used"]))
    sys.stdout.write(table(rows) if len(rows) > 1 else "")
    return code


def lfs_quota(argv: list[str], mounts: list[dict]) -> int:
    human_ = "-h" in argv
    args = [a for a in argv if a != "-h"]
    user = getpass.getuser()
    if "-u" in args:
        i = args.index("-u")
        user = args[i + 1] if i + 1 < len(args) else user
        args = args[:i] + args[i + 2:]
    if any(a in ("-g", "-p") for a in args):
        sys.stderr.write("lfs quota: this simulation answers -u only\n")
        return 1
    paths = [a for a in args if not a.startswith("-")]
    chosen = lfs_mounts(mounts, paths[0] if paths else None)
    if not chosen:
        sys.stderr.write("lfs quota: no such Lustre mount\n")
        return 1
    try:
        uid = pwd.getpwnam(user).pw_uid
    except KeyError:
        sys.stderr.write(f"lfs quota: unknown user {user}\n")
        return 1
    for m in chosen:
        q = m["quotas"].get(user)
        if q is None:
            sys.stderr.write(f"lfs quota: no quota record for {user} on {m['path']}\n")
            return 1
        used_kb, lim_kb = q["used_gb"] * 1024 * 1024, q["quota_gb"] * 1024 * 1024
        f = (lambda kb: human(kb * 1024)) if human_ else (lambda kb: str(int(kb)))
        over = q["exceeded"]
        grace = "6d23h59m59s" if over else "-"  # the Lustre manual's form: days, hours, minutes, seconds
        print(f"Disk quotas for user {user} (uid {uid}):")  # "user" and the kbytes column are the Lustre manual's; newer releases print usr and used
        print(table([["Filesystem", "bytes" if human_ else "kbytes", "quota", "limit", "grace", "files", "quota", "limit", "grace"],
                     [m["path"], f(used_kb) + ("*" if over else ""), f(lim_kb), f(lim_kb), grace, str(q["files_used"]), str(q["quota_files"]), str(q["quota_files"]), "-"]]), end="")
        if over:
            sys.stderr.write(f"lfs quota: user {user} is over the quota on {m['path']}\n")
    return 0


def lfs(argv: list[str]) -> int:
    mounts = view()
    if mounts is None:
        sys.stderr.write("lfs: the storage simulation does not answer (is the GPU exporter running?)\n")
        return 1
    sub, rest = (argv[0], argv[1:]) if argv else ("", [])
    if sub == "df":
        return lfs_df(rest, mounts)
    if sub == "quota":
        return lfs_quota(rest, mounts)
    sys.stderr.write(f"lfs: {sub or 'no command'}: not simulated here (df and quota are)\n")
    return 1


if __name__ == "__main__":
    sys.exit((lfs if os.path.basename(sys.argv[0]) == "lfs" else df)(sys.argv[1:]))
