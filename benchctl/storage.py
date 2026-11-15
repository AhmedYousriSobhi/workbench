"""Declared storage: the mounts a cluster shows (`storage:` in sim.yaml, issue 461, ADR 0078).

No NFS, Lustre or vendor appliance runs here (SPEC C-11: they need root and kernel modules). A mount is a declaration: a path, the
kind of file system it stands for, a size, how full it is and how many inodes it has. The GPU exporter turns it into
`node_filesystem_*` series per node, `df` and `lfs` shims on every Slurm and login node print it in the shape the real system uses, and
scenarios fill it up. A mount marked `shared` is also one real directory on every Slurm and login node (a Docker volume, or a host
directory named by `host_path`), which is where users keep datasets; its `df` size is still the declared one, but what can be written
is the host's free space. With no `storage:` section the two built-in mounts below apply.
"""
import posixpath
import subprocess

KINDS = {"nfs": "nfs4", "lustre": "lustre", "ddn": "lustre", "local": "ext4"}  # kind -> the file system type `df -T` shows
RESERVED = ("/", "/etc", "/usr", "/bin", "/sbin", "/lib", "/proc", "/sys", "/dev", "/run", "/var", "/boot", "/opt", "/root", "/tmp", "/mnt")
DEFAULTS = [
    {"path": "/home", "kind": "nfs", "source": "nas01:/export/home", "size_tb": 20, "used_pct": 35, "inodes": 2_000_000_000,
     "inodes_used_pct": 30, "shared": False, "quota": {"gb": 100, "files": 1_000_000}},
    {"path": "/scratch", "kind": "lustre", "source": "mgs01@tcp:/scratch", "size_tb": 500, "used_pct": 40, "inodes": 4_000_000_000,
     "inodes_used_pct": 25, "shared": True, "quota": {"gb": 20_000, "files": 50_000_000}},
]
KEYS = {"path", "kind", "source", "size_tb", "used_pct", "inodes", "inodes_used_pct", "shared", "host_path", "quota"}


class StorageError(ValueError):
    pass


def _num(m: dict, key: str, lo: float, hi: float | None = None) -> float:
    v = m[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or v < lo or (hi is not None and v > hi):
        raise StorageError(f"storage mount {m['path']}: {key} must be a number from {lo} to {hi if hi is not None else 'any'}, not {v!r}")
    return v


def _mount(raw: dict, i: int) -> dict:
    if not isinstance(raw, dict) or not isinstance(raw.get("path"), str):
        raise StorageError(f"storage mount {i + 1}: each mount needs a `path`")
    path = posixpath.normpath(raw["path"])
    if not path.startswith("/") or path in RESERVED or any(path.startswith(r + "/") for r in RESERVED[1:] if r not in ("/mnt", "/opt")):
        raise StorageError(f"storage mount {raw['path']!r}: an absolute path that is not a system directory (not {', '.join(RESERVED[:6])} ...)")
    if unknown := sorted(set(raw) - KEYS):
        raise StorageError(f"storage mount {path}: unknown key(s) {', '.join(unknown)}; known: {', '.join(sorted(KEYS))}")
    kind = raw.get("kind", "nfs")
    if kind not in KINDS:
        raise StorageError(f"storage mount {path}: kind must be one of {', '.join(KINDS)}, not {kind!r}")
    m = {"path": path, "kind": kind, "fstype": KINDS[kind], "source": str(raw.get("source") or f"{kind}01:{path}"),
         "size_tb": _num({**raw, "path": path, "size_tb": raw.get("size_tb", 10)}, "size_tb", 0.001),
         "used_pct": _num({**raw, "path": path, "used_pct": raw.get("used_pct", 40)}, "used_pct", 0, 100),
         "inodes": int(_num({**raw, "path": path, "inodes": raw.get("inodes", 1_000_000_000)}, "inodes", 1000)),
         "inodes_used_pct": _num({**raw, "path": path, "inodes_used_pct": raw.get("inodes_used_pct", 20)}, "inodes_used_pct", 0, 100),
         "shared": bool(raw.get("shared", False))}
    q = raw.get("quota") or {}
    if not isinstance(q, dict) or set(q) - {"gb", "files"}:
        raise StorageError(f"storage mount {path}: quota takes gb and files")
    m["quota"] = {"gb": int(_num({"path": path, "gb": q.get("gb", 100)}, "gb", 1)), "files": int(_num({"path": path, "files": q.get("files", 1_000_000)}, "files", 1))}
    hp = raw.get("host_path")
    if hp is not None:
        if not m["shared"] or not isinstance(hp, str) or not hp.startswith("/") or ".." in hp.split("/"):
            raise StorageError(f"storage mount {path}: host_path is an absolute directory and only for a shared mount")
        m["host_path"] = hp
    return m


def config(sim: dict) -> list[dict]:
    """The mounts, validated: `storage.mounts` from sim.yaml, else the two built-in ones. Raises StorageError with one message per mistake."""
    raw = (sim.get("storage") or {}).get("mounts")
    mounts = [_mount(m, i) for i, m in enumerate(DEFAULTS if raw is None else raw)]
    seen: set[str] = set()
    for m in mounts:
        if m["path"] in seen:
            raise StorageError(f"storage mount {m['path']} is declared twice")
        seen.add(m["path"])
    return mounts


def shared(sim: dict) -> list[dict]:
    return [m for m in config(sim) if m["shared"]]


def volume_name(path: str) -> str:
    return "shared-" + path.strip("/").replace("/", "-")


def volumes(sim: dict, mounts: list[dict] | None = None) -> tuple[dict[str, dict], list[str]]:
    """(top-level compose volumes, the mount strings for a node): a named volume per shared mount, or a bind to its `host_path`."""
    named, mounts_out = {}, []
    for m in mounts if mounts is not None else shared(sim):
        if "host_path" in m:
            mounts_out.append(f"{m['host_path']}:{m['path']}")
        else:
            named[volume_name(m["path"])] = {}
            mounts_out.append(f"{volume_name(m['path'])}:{m['path']}")
    return named, mounts_out


def inventory_block(sim: dict) -> dict:
    """What the exporter and the shims read from `inventory.json`: every mount with its declared numbers (no host paths)."""
    return {"mounts": [{k: v for k, v in m.items() if k != "host_path"} for m in config(sim)]}


def reset(sim: dict, runner=subprocess.run) -> list[str]:
    """Remove the named volumes of the shared mounts (the datasets and job output in them); a `host_path` is never touched. Returns their names."""
    names = [f"workbench_{n}" for n in volumes(sim)[0]]
    if names:
        runner(["docker", "volume", "rm", "-f", *names], capture_output=True, text=True, check=False)
    return names
