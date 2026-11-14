"""Which Slurm the cluster runs (issue 221, ADR 0079): the pinned version by default, or one from a short table.

`benchctl up --slurm-version X` (or `slurm.version: X` in sim.yaml) builds the node, controller and login images from that release
and tags them `:dev-X`; the pinned version keeps the plain `:dev` tags, so a default cluster is exactly what it was. Only versions in
`VERSIONS` are accepted, each with the SHA256 of its tarball (computed from `download.schedmd.com`, which publishes none, ADR 0002).
"""
from __future__ import annotations

import hashlib
import re
import time
import urllib.request

URL = "https://download.schedmd.com/slurm/slurm-{v}.tar.bz2"
MAX_BYTES = 100 * 1024 * 1024  # the real tarballs are about 7 MB; anything larger is refused
MAX_SECONDS = 300

DEFAULT = "26.05.5"  # the pin in images/node-base/Dockerfile (ARG SLURM_VERSION); tests/test_slurmver.py keeps them equal
VERSIONS: dict[str, str] = {
    "25.05.9": "7be633820df896df819ac2e9f2ebd0824de509515e5fb78ba1c19da2453b39ed",
    "25.11.9": "d238138c6c029045f3764b75f142171266992d07f47970e089fae386b1031cdb",
    "26.05.5": "4680cafc82b0689865c885eb703441e7ebf764896c40fd03a50f2fcb60664f68",
    "26.05.6": "386a0d624fa7c730cf4dfa9171bf6dc66b6816b5683242c5271879999a54a9a7",
}
BASES = ("workbench/node-base", "workbench/slurm-ctl", "workbench/login")  # the images that carry Slurm


class SlurmVersionError(ValueError):
    pass


def version(sim: dict) -> str:
    return str((sim.get("slurm") or {}).get("version") or DEFAULT)


def problems(sim: dict) -> list[str]:
    v = version(sim)
    if v in VERSIONS:
        return []
    return [f"slurm.version is {v}: use one of {', '.join(sorted(VERSIONS))} (for another release run `benchctl get slurm-checksum X` and paste the line it prints into benchctl/slurmver.py, docs/guides/slurm-version.md)"]


def tag(sim: dict) -> str:
    """`dev` for the pinned version, `dev-X` for another: each version keeps its own cached images."""
    v = version(sim)
    return "dev" if v == DEFAULT else f"dev-{v}"


def retag(image: str, sim: dict) -> str:
    """The image name with the version's tag, for the three images that carry Slurm; any other image unchanged."""
    name, _, t = image.partition(":")
    return f"{name}:{tag(sim)}" if name in BASES and t == "dev" else image


def build_args(sim: dict, derived: bool) -> list[str]:
    """`docker compose build` arguments: the node base takes the version and checksum, the images built FROM it take its tag.
    Nothing for the pinned version (the Dockerfiles' defaults are it)."""
    v = version(sim)
    if v == DEFAULT:
        return []
    return ["--build-arg", f"BASE_TAG={tag(sim)}"] if derived else ["--build-arg", f"SLURM_VERSION={v}", "--build-arg", f"SLURM_SHA256={VERSIONS[v]}"]


def _opener(url: str):
    return urllib.request.urlopen(url, timeout=30)


def fetch_sha256(v: str, opener=_opener, max_bytes: int = MAX_BYTES, max_seconds: float = MAX_SECONDS) -> str:
    """Download SchedMD's tarball for release `v` (bounded in size and time) and return its SHA256. Nothing is written to disk."""
    if not re.fullmatch(r"\d{2}\.\d{2}\.\d+", v):
        raise SlurmVersionError(f"{v} is not a Slurm release number (expected like 25.11.9)")
    h, n, t0 = hashlib.sha256(), 0, time.monotonic()
    try:
        with opener(URL.format(v=v)) as r:
            while chunk := r.read(1 << 20):
                n += len(chunk)
                if n > max_bytes:
                    raise SlurmVersionError(f"slurm-{v}.tar.bz2 is larger than {max_bytes // 1048576} MB: not hashed")
                if time.monotonic() - t0 > max_seconds:
                    raise SlurmVersionError(f"download of slurm-{v}.tar.bz2 took longer than {int(max_seconds)} s: not hashed")
                h.update(chunk)
    except SlurmVersionError:
        raise
    except Exception as e:  # network error, 404 for a release that does not exist
        raise SlurmVersionError(f"could not download {URL.format(v=v)}: {e}") from e
    if n == 0:
        raise SlurmVersionError(f"{URL.format(v=v)} returned no data")
    return h.hexdigest()


def table_line(v: str, sha: str) -> str:
    return f'    "{v}": "{sha}",'


def check(v: str, sha: str) -> str:
    """Raise unless `sha` is the checksum recorded for `v`; return a message when it is."""
    rec = VERSIONS.get(v)
    if rec is None:
        raise SlurmVersionError(f"{v} has no recorded SHA256: add the line `{table_line(v, sha)}` to VERSIONS in benchctl/slurmver.py after checking it (docs/guides/slurm-version.md)")
    if rec != sha:
        raise SlurmVersionError(f"{v}: the tarball hashes to {sha} but {rec} is recorded: do not build it; check the recorded value and the download")
    return f"{v}: SHA256 matches the recorded value"
