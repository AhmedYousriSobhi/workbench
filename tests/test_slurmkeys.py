"""The munge and JWT keys are made by `up` and mounted, never baked into an image (issue 465, SEC1)."""
import re
import stat
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import compose, stack

ROOT = Path(__file__).resolve().parents[1]
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def test_keys_are_made_once_private_and_kept(tmp_path):
    d = stack.ensure_slurm_keys(tmp_path / "slurm")
    munge, jwt = (d / "munge.key").read_bytes(), (d / "jwt_hs256.key").read_bytes()
    assert len(munge) == 1024 and len(jwt) == 32 and munge != bytes(1024)
    assert stat.S_IMODE((d / "munge.key").stat().st_mode) == 0o600 and stat.S_IMODE(d.stat().st_mode) == 0o700
    stack.ensure_slurm_keys(d)  # a new key would break every running node until it restarts
    assert (d / "munge.key").read_bytes() == munge and (d / "jwt_hs256.key").read_bytes() == jwt
    other = stack.ensure_slurm_keys(tmp_path / "other")
    assert (other / "munge.key").read_bytes() != munge  # no two clones share keys


@pytest.mark.parametrize("profile", ["min", "std"])
def test_every_container_on_a_slurm_image_mounts_the_keys_read_only(profile):
    svcs = compose.generate(SIM, compose.load_profile(profile))["services"]
    on_image = {n: s for n, s in svcs.items() if s.get("image") in compose.SLURM_KEY_IMAGES}
    assert {"slurmctld", "slurmrestd", "cn001", "login"} <= set(on_image)
    for name, svc in on_image.items():
        assert compose.SLURM_KEYS_MOUNT in svc["volumes"], name
    assert compose.SLURM_KEYS_MOUNT.endswith(":ro")
    assert not any(compose.SLURM_KEYS_MOUNT in s.get("volumes", []) for n, s in svcs.items() if n not in on_image)


def test_the_accounting_services_mount_them_too():
    svcs = compose.generate(SIM, compose.load_profile("std"))["services"]
    assert compose.SLURM_KEYS_MOUNT in svcs["slurmdbd"]["volumes"] and compose.SLURM_KEYS_MOUNT in svcs["accounting-init"]["volumes"]


def test_the_image_has_no_key_material_and_the_entrypoint_installs_the_mounted_ones():
    code = "\n".join(line for line in (ROOT / "images/node-base/Dockerfile").read_text().splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"urandom|create-munge-key|munge\.key|jwt_hs256\.key", code), "a key is generated in the image"
    entry = (ROOT / "images/node-base/entrypoint.sh").read_text()
    assert "install -o munge -g munge -m 0400 /run/workbench/slurm/munge.key /etc/munge/munge.key" in entry
    assert "install -o slurm -g slurmjwt -m 0640 /run/workbench/slurm/jwt_hs256.key" in entry
    assert entry.index("munge.key /etc/munge") < entry.index('munged --force')  # the key is in place before munged starts
    assert "benchctl up" in entry  # one message when the mount is missing


def test_up_makes_the_keys_before_it_starts_the_slurm_containers(tmp_path, monkeypatch):
    import shutil
    monkeypatch.chdir(tmp_path)
    for d in ("profiles", "components", "images"):
        shutil.copytree(ROOT / d, tmp_path / d, dirs_exist_ok=True)
    events = []
    monkeypatch.setattr(stack, "ensure_slurm_keys", lambda *a: events.append("keys") or tmp_path)
    monkeypatch.setattr(stack, "ensure_ssh_keys", lambda *a, **k: tmp_path)

    class FirstStart(Exception):
        pass

    def runner(cmd):
        if "up" in cmd:  # the first container start: stop here, the order is what is tested
            events.append("up")
            raise FirstStart
        return subprocess.CompletedProcess(cmd, 0, "", "")

    with pytest.raises(FirstStart):
        stack.up(SIM, "min", runner=runner, say=lambda m: None)
    assert "keys" in events and events.index("keys") < events.index("up")
