"""Tab completion in a real bash: install into a throwaway HOME, source its rc file, ask for candidates (#105)."""
import os
import subprocess

import pytest
from helpers import ROOT


@pytest.fixture(scope="module")
def home(tmp_path_factory):
    h = tmp_path_factory.mktemp("home")
    (h / ".bashrc").write_text("# user rc\n")
    env = {**os.environ, "HOME": str(h), "SHELL": "/bin/bash"}
    r = subprocess.run([str(ROOT / "scripts/benchctl"), "completion", "install"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return h, env


def candidates(home, *words: str, cword: int) -> list[str]:
    _h, env = home
    arr = " ".join(f"'{w}'" for w in words)
    script = (f"source ~/.bashrc; COMP_WORDS=({arr}); COMP_CWORD={cword}; "
              "_benchctl_completion ./scripts/benchctl; printf '%s\\n' \"${COMPREPLY[@]}\"")
    r = subprocess.run(["bash", "-c", script], cwd=ROOT, env=env, capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0, r.stderr
    return r.stdout.split()


def test_install_wrote_one_line_and_a_working_link(home):
    h, _ = home
    assert (h / ".bashrc").read_text().count("workbench completion") == 2  # the open and close marks
    assert (h / ".local/bin/benchctl").is_symlink()
    r = subprocess.run([str(h / ".local/bin/benchctl"), "get", "profiles", "-o", "name"], cwd=ROOT,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and "min" in r.stdout.split()  # the link finds the repo and its venv


def test_tab_completes_verbs_resources_and_values(home):
    assert "describe" in candidates(home, "benchctl", "d", cword=1)
    assert {"nodes", "services", "profiles"} <= set(candidates(home, "benchctl", "get", "", cword=2))
    assert candidates(home, "benchctl", "up", "--profile", "mi", cword=3) == ["min"]
    assert candidates(home, "benchctl", "describe", "node", "gpu", cword=3) == ["gpu001", "gpu002"]
    assert candidates(home, "benchctl", "get", "nodes", "-l", "kind=g", cword=4) == ["kind=gpu"]
    assert candidates(home, "benchctl", "scenario", "run", "node-d", cword=3) == ["node-down"]


def test_remove_undoes_it(home):
    h, env = home
    r = subprocess.run([str(ROOT / "scripts/benchctl"), "completion", "remove"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=60, check=False)
    assert r.returncode == 0 and (h / ".bashrc").read_text().strip() == "# user rc"
    assert not (h / ".local/bin/benchctl").exists()
