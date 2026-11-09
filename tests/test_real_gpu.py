"""The real GPU part (issue 428, SPEC C-23): detection, the refusal message, and the wiring that keeps the container unprivileged."""
import subprocess
from pathlib import Path

import yaml

from benchctl import compose, real_gpu, workbench

ROOT = Path(__file__).resolve().parent.parent
SIM = yaml.safe_load((ROOT / "sim.yaml").read_text())


def smi(out: str, rc: int = 0):
    return lambda cmd: subprocess.CompletedProcess(cmd, rc, out, "")


def test_a_card_is_found_from_nvidia_smi_output():
    assert real_gpu.cards(smi("GPU 0: NVIDIA GeForce RTX 3050 Laptop GPU (UUID: GPU-abc)\n")) == ["GPU 0: NVIDIA GeForce RTX 3050 Laptop GPU"]
    assert real_gpu.cards(smi("", 9)) == [] and real_gpu.cards(smi("No devices were found\n", 6)) == []


def test_a_missing_tool_means_no_card_not_a_crash():
    def boom(cmd):
        raise FileNotFoundError(cmd[0])
    assert real_gpu.cards(boom) == []


def test_no_card_and_no_cdi_spec_are_two_different_messages(tmp_path):
    assert "needs an NVIDIA card" in real_gpu.problems(smi("", 9), (tmp_path / "a.yaml",))[0]
    assert "no CDI spec" in real_gpu.problems(smi("GPU 0: X (UUID: u)\n"), (tmp_path / "a.yaml",))[0]
    spec = tmp_path / "nvidia.yaml"
    spec.write_text("cdiVersion: 0.5.0\n")
    assert real_gpu.problems(smi("GPU 0: X (UUID: u)\n"), (spec,)) == []


def services(on: bool):
    return compose.generate(SIM, {"groups": ["core", "real-gpu"] if on else ["core"], "compute_nodes": 1})["services"]


def test_off_by_default_and_by_name_only():
    assert real_gpu.SERVICE not in services(False)
    assert "real-gpu" in workbench.BY_NAME_ONLY and "real-gpu" in workbench.PART_CATEGORY and "real-gpu" in workbench.PART_INFO


def test_the_card_comes_in_as_a_device_not_as_privileges():
    svc = services(True)[real_gpu.SERVICE]
    assert svc["devices"] == [real_gpu.DEVICE] and svc["environment"]["DCGM_HOSTNAME"] == real_gpu.HOSTNAME and "hostname" not in svc
    assert svc["ports"] == [f"127.0.0.1:{real_gpu.PORT}:9400"]
    assert not {"privileged", "cap_add", "security_opt", "runtime"} & set(svc)
    assert "MOCK_NVML_PROFILE" not in svc.get("environment", {})  # no mock profile: start.sh runs on the real driver
    assert svc["mem_limit"] == f"{real_gpu.LIMIT_MB}m" and svc["labels"]["workbench.class"] == "P6"

