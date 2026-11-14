"""The systemd-oomd backstop (SPEC GV-10, ADR 0010 addendum): drop-in text, installer dry run, doctor line. Hermetic."""
import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from benchctl import checks

ROOT = Path(__file__).resolve().parents[1]
DROPIN = ROOT / "systemd/workbench.slice.d/50-oomd.conf"
SCRIPT = str(ROOT / "scripts/install-governor.sh")


def keys(path: Path) -> dict:
    return dict(ln.split("=", 1) for ln in path.read_text().splitlines() if "=" in ln and not ln.startswith("#"))


def test_dropin_sets_pressure_kill_on_the_project_slice_only():
    assert keys(DROPIN) == {"ManagedOOMMemoryPressure": "kill", "ManagedOOMMemoryPressureLimit": "60%"}
    assert DROPIN.parent.name == "workbench.slice.d"  # the compose cgroup_parent slices nest under workbench.slice
    assert "[Slice]" in DROPIN.read_text()


@pytest.mark.skipif(not shutil.which("systemd-analyze"), reason="no systemd-analyze")
def test_dropin_passes_systemd_analyze(tmp_path):
    slice_unit = tmp_path / "workbench.slice"
    slice_unit.write_text("[Unit]\nDescription=t\n\n[Slice]\n" + DROPIN.read_text().split("[Slice]")[1])
    out = subprocess.run(["systemd-analyze", "verify", str(slice_unit)], capture_output=True, text=True, check=False).stderr
    assert "Unknown key" not in out and "Failed" not in out, out


def dry(tmp_path, *args):
    env = {**os.environ, "WORKBENCH_DRY_RUN": "1", "WORKBENCH_OOMD_DIR": str(tmp_path / "workbench.slice.d")}
    return subprocess.run([SCRIPT, *args], capture_output=True, text=True, env=env, check=False)


def test_installer_prints_the_dropin_without_root(tmp_path):
    r = dry(tmp_path, "--print-oomd")
    assert r.returncode == 0 and "ManagedOOMMemoryPressure=kill" in r.stdout and "workbench.slice.d/50-oomd.conf" in r.stdout
    assert not (tmp_path / "workbench.slice.d").exists()


def test_installer_installs_then_removes_only_the_dropin(tmp_path):
    assert dry(tmp_path, "--oomd-only").returncode == 0
    conf = tmp_path / "workbench.slice.d/50-oomd.conf"
    assert conf.read_text() == DROPIN.read_text()
    assert dry(tmp_path, "--without-oomd").returncode == 0
    assert not conf.exists()


def test_installer_never_names_the_global_oomd_config():
    code = "\n".join(ln for ln in Path(SCRIPT).read_text().splitlines() if not ln.lstrip().startswith("#"))
    assert "/etc/systemd/oomd.conf" not in code and "oomd.conf.d" not in code


def test_a_plain_install_does_not_add_the_backstop():
    text = Path(SCRIPT).read_text()
    assert "want_oomd=0" in text and '[ "$want_oomd" = 1 ] && add_oomd' in text  # off unless asked


def fake(active="active", prop="kill"):
    def runner(cmd):
        return SimpleNamespace(stdout=active if cmd[1] == "is-active" else prop, returncode=0)
    return runner


def test_doctor_off_by_default(tmp_path):
    c = checks.check_oomd(tmp_path / "none.conf", runner=fake())
    assert c.status == checks.OK and "off" in c.detail and "--with-oomd" in c.fix


def test_doctor_active(tmp_path):
    f = tmp_path / "x.conf"
    f.write_text("x")
    c = checks.check_oomd(f, runner=fake())
    assert c.status == checks.OK and "active" in c.detail


def test_doctor_warns_when_oomd_not_running(tmp_path):
    f = tmp_path / "x.conf"
    f.write_text("x")
    c = checks.check_oomd(f, runner=fake(active="inactive"))
    assert c.status == checks.WARN and "systemd-oomd" in c.fix


def test_doctor_warns_when_not_applied(tmp_path):
    f = tmp_path / "x.conf"
    f.write_text("x")
    c = checks.check_oomd(f, runner=fake(prop="auto"))
    assert c.status == checks.WARN and "daemon-reload" in c.fix
