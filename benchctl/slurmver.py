"""Which Slurm the cluster runs (issue 221, ADR 0079): the pinned version by default, or one from a short table.

`benchctl up --slurm-version X` (or `slurm.version: X` in sim.yaml) builds the node, controller and login images from that release
and tags them `:dev-X`; the pinned version keeps the plain `:dev` tags, so a default cluster is exactly what it was. Only versions in
`VERSIONS` are accepted, each with the SHA256 of its tarball (computed from `download.schedmd.com`, which publishes none, ADR 0002).
"""
from __future__ import annotations

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
    return [f"slurm.version is {v}: use one of {', '.join(sorted(VERSIONS))} (a version outside the list needs its SHA256 added to benchctl/slurmver.py, ADR 0079)"]


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
