"""The tutorials (docs/tutorials, issue 936): every page is in the index, every link resolves, and every benchctl command it shows exists."""

import re
from pathlib import Path

import pytest
from typer.testing import CliRunner

from benchctl.cli import app

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "docs/tutorials"
PAGES = sorted(p for p in DIR.glob("*.md") if p.name != "README.md")
SECTIONS = ("You will:", "## Steps", "## You should see", "## Next")


def test_there_are_tutorials_and_each_is_in_the_index():
    index = (DIR / "README.md").read_text()
    assert len(PAGES) >= 10
    for p in PAGES:
        assert f"]({p.name})" in index, p.name


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_each_tutorial_has_its_parts_and_its_links_resolve(page):
    text = page.read_text()
    for s in SECTIONS:
        assert s in text, (page.name, s)
    for target in re.findall(r"\]\(([^)#]+\.md)(?:#[^)]*)?\)", text):
        assert (page.parent / target).resolve().is_file(), (page.name, target)


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_every_benchctl_command_shown_exists(page):
    """The verb of every `benchctl VERB ...` line in a code block answers --help."""
    runner = CliRunner()
    code = "\n".join(re.findall(r"```bash\n(.*?)```", page.read_text(), re.DOTALL))
    for line in code.splitlines():
        m = re.search(r"(?:\./scripts/)?benchctl ((?:[a-z][\w-]*)(?: [a-z][\w-]*)?)", line)
        if not m or line.lstrip().startswith("#"):
            continue
        verb = m.group(1).split()[0]
        assert runner.invoke(app, [verb, "--help"]).exit_code == 0, (page.name, line)


def _stateful_run(tmp_path, slurm, temp, util, now):
    """Run the stateful check of tutorial 24 through the real runner with a fake sinfo; returns the lines it printed."""
    import os
    import re
    import subprocess

    page = (DIR / "24-write-a-stateful-health-check.md").read_text()
    script = re.search(r"<<'SH'\n(.*?)\n\s*SH\n", page, re.DOTALL).group(1)
    script = "\n".join(line.removeprefix("   ") for line in script.splitlines()) + "\n"
    checks = tmp_path / "checks"
    checks.mkdir(exist_ok=True)
    (checks / "idle-hot.sh").write_text(script)
    (checks / "checks.conf").write_text("idle-hot on idle_temp=50 confirm_s=60 ceiling=87\n")
    bin_ = tmp_path / "bin"
    bin_.mkdir(exist_ok=True)
    (bin_ / "sinfo").write_text(f"#!/bin/sh\necho {slurm}\n")
    (bin_ / "date").write_text(f"#!/bin/sh\necho {now}\n")
    (bin_ / "scontrol").write_text("#!/bin/sh\nexit 0\n")
    for tool in (bin_ / "sinfo", bin_ / "date", bin_ / "scontrol"):
        tool.chmod(0o755)
    m = tmp_path / "m.txt"
    m.write_text(
        f'DCGM_FI_DEV_GPU_TEMP{{node="gpu001",gpu="0",Hostname="gpu001"}} {temp}\n'
        f'DCGM_FI_DEV_GPU_UTIL{{node="gpu001",gpu="0",Hostname="gpu001"}} {util}\n'
    )
    env = {
        **os.environ,
        "PATH": f"{bin_}:{os.environ['PATH']}",
        "NHC_METRICS_FILE": str(m),
        "NHC_SCONTROL": str(bin_ / "scontrol"),
        "NHC_NODE": "gpu001",
        "NHC_EXPECTED_GPUS": "1",
        "NHC_STATE_DIR": str(tmp_path),
        "NHC_CHECKS_DIR": str(checks),
        "NHC_ENV_FILE": str(tmp_path / "none"),
    }
    r = subprocess.run(
        ["bash", str(ROOT / "images/node-base/nhc")],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return r.stdout


def test_stateful_check_waits_then_fails_then_clears(tmp_path):
    state = tmp_path / "nhc-state-gpu001-idle-hot"
    assert _stateful_run(
        tmp_path, "idle", "60.0", "1", 1000
    ) == "" and state.read_text().startswith("SUSPECT 1000")
    assert _stateful_run(tmp_path, "idle", "60.0", "1", 1030) == ""
    out = _stateful_run(tmp_path, "idle", "60.0", "1", 1061)
    assert "idle GPU at 60C" in out and state.read_text().startswith("FAULT 1000")
    assert _stateful_run(tmp_path, "idle", "40.0", "1", 1100) == "" and not state.exists()


def test_stateful_check_uses_only_the_ceiling_with_a_job(tmp_path):
    assert _stateful_run(tmp_path, "allocated", "80.0", "90", 1000) == ""
    assert "over the ceiling 87C" in _stateful_run(tmp_path, "mixed", "92.0", "90", 1000)
    assert _stateful_run(tmp_path, "idle", "60.0", "90", 1000) == ""  # busy GPUs are not "idle"


def test_stateful_check_keeps_the_drain_while_hot_and_clears_when_cool(tmp_path):
    state = tmp_path / "nhc-state-gpu001-idle-hot"
    _stateful_run(tmp_path, "idle", "60.0", "1", 1000)
    assert "unhealthy" in _stateful_run(tmp_path, "idle", "60.0", "1", 1061)
    assert (
        "still at 60C" in _stateful_run(tmp_path, "drained", "60.0", "1", 1100) and state.exists()
    )
    assert _stateful_run(tmp_path, "drained", "40.0", "1", 1130) == "" and not state.exists()
