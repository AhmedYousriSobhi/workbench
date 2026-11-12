"""The generated compose file is accepted by the real `docker compose config` (moved from tests/test_compose.py: a unit test must not run docker, #473)."""
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from benchctl import compose

SIM = yaml.safe_load(Path("sim.yaml").read_text())


@pytest.mark.skipif(not shutil.which("docker"), reason="docker missing")
def test_docker_compose_accepts_generated_file():
    out = compose.write(compose.generate(SIM, compose.load_profile("std")), Path("compose/generated/test-std.yml"))
    try:
        r = subprocess.run(["docker", "compose", "-f", str(out), "config", "-q"], capture_output=True, text=True, timeout=60, check=False)
        assert r.returncode == 0, r.stderr
    finally:
        out.unlink()
