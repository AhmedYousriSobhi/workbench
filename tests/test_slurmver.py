"""A selectable Slurm version (issue 221, ADR 0079)."""
import re
from pathlib import Path

import pytest
import yaml

from benchctl import compose, slurmver, stack, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def with_version(v):
    return {**SIM, "slurm": {**(SIM.get("slurm") or {}), "version": v}}


def test_the_default_is_the_dockerfiles_pin_and_its_checksum():
    text = (ROOT / "images/node-base/Dockerfile").read_text()
    assert f"ARG SLURM_VERSION={slurmver.DEFAULT}" in text
    assert f"ARG SLURM_SHA256={slurmver.VERSIONS[slurmver.DEFAULT]}" in text


def test_the_table_holds_the_supported_releases_with_well_formed_checksums():
    assert {"25.05.9", "25.11.9", "26.05.5", "26.05.6"} <= set(slurmver.VERSIONS)
    assert all(re.fullmatch(r"[0-9a-f]{64}", s) for s in slurmver.VERSIONS.values())
    assert len(set(slurmver.VERSIONS.values())) == len(slurmver.VERSIONS)


def test_a_version_outside_the_table_is_refused_with_one_message():
    assert slurmver.problems(SIM) == []
    msg = slurmver.problems(with_version("24.11.1"))
    assert len(msg) == 1 and "24.11.1" in msg[0] and "25.11.9" in msg[0]
    with pytest.raises(slurmver.SlurmVersionError):
        compose.generate(with_version("24.11.1"), {"groups": ["core"]})


def test_the_pinned_version_keeps_its_tags_and_another_gets_its_own():
    out = compose.generate(SIM, {"groups": ["core", "access"], "compute_nodes": 1})["services"]
    assert out["slurmctld"]["image"] == "workbench/slurm-ctl:dev" and out["login"]["image"] == "workbench/login:dev"
    other = compose.generate(with_version("25.11.9"), {"groups": ["core", "access"], "compute_nodes": 1})["services"]
    assert other["slurmctld"]["image"] == "workbench/slurm-ctl:dev-25.11.9" and other["login"]["image"] == "workbench/login:dev-25.11.9"
    assert next(s["image"] for n, s in other.items() if s["labels"].get("workbench.node")) == "workbench/node-base:dev-25.11.9"
    assert other["login"]["volumes"] and any("secrets/slurm" in v for v in other["slurmctld"]["volumes"])  # the key mount still finds the retagged image


def test_build_args_are_empty_for_the_pin_and_carry_version_and_checksum_otherwise():
    assert slurmver.build_args(SIM, False) == [] and slurmver.build_args(SIM, True) == []
    base = slurmver.build_args(with_version("25.11.9"), False)
    assert base == ["--build-arg", "SLURM_VERSION=25.11.9", "--build-arg", f"SLURM_SHA256={slurmver.VERSIONS['25.11.9']}"]
    assert slurmver.build_args(with_version("25.11.9"), True) == ["--build-arg", "BASE_TAG=dev-25.11.9"]


def test_the_derived_dockerfiles_take_the_base_tag():
    for d in ("slurm-ctl", "login"):
        text = (ROOT / f"images/{d}/Dockerfile").read_text()
        assert "ARG BASE_TAG=dev" in text and "FROM workbench/node-base:${BASE_TAG}" in text


def test_the_version_is_part_of_every_slurm_images_build_inputs(tmp_path):
    base, ctl = tmp_path / "images/node-base", tmp_path / "images/slurm-ctl"
    for d, f in ((base, "FROM ubuntu\n"), (ctl, "FROM workbench/node-base:${BASE_TAG}\n")):
        d.mkdir(parents=True)
        (d / "Dockerfile").write_text(f)
    services = {"cn001": {"build": "../../images/node-base", "image": "x"}, "slurmctld": {"build": "../../images/slurm-ctl", "image": "y"}}
    path = tmp_path / "compose/generated/docker-compose.yml"
    path.parent.mkdir(parents=True)
    a = stack.image_hashes(["cn001", "slurmctld"], services, path, "cn001", "26.05.5")
    b = stack.image_hashes(["cn001", "slurmctld"], services, path, "cn001", "25.11.9")
    assert a["cn001"][0] != b["cn001"][0] and a["slurmctld"][0] != b["slurmctld"][0]


def test_the_selection_carries_the_version_into_the_simulation_and_is_checked():
    spec = workbench.defaults(SIM)
    assert spec.slurm_version is None and workbench.effective(SIM, spec)[0].get("slurm", {}).get("version") is None
    spec.slurm_version = "25.11.9"
    assert workbench.effective(SIM, spec)[0]["slurm"]["version"] == "25.11.9"
    assert workbench.from_dict(spec.to_dict(), workbench.defaults(SIM)).slurm_version == "25.11.9"
    spec.slurm_version = "1.2.3"
    assert any("1.2.3" in p for p in spec.problems())
