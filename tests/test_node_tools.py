"""Basic Linux troubleshooting tools in the node base image (issue 441)."""
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parent.parent / "images/node-base/Dockerfile").read_text()
RUNTIME = DOCKERFILE.split("FROM ubuntu:24.04")[-1]
PACKAGES = ("iputils-ping", "iproute2", "dnsutils", "netcat-openbsd", "traceroute", "tcpdump", "net-tools", "curl", "procps", "lsof", "strace", "vim-tiny")


def test_the_runtime_stage_installs_the_tools_a_bare_metal_machine_has_in_its_one_apt_layer():
    assert RUNTIME.count("apt-get install") == 1  # one layer (ADR 0014 addendum)
    for pkg in PACKAGES:
        assert pkg in RUNTIME.split("apt-get install")[1].split("&&")[0], pkg
