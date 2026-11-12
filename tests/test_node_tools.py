"""Basic Linux troubleshooting tools in the node base image (issue 441)."""
from pathlib import Path

DOCKERFILE = (Path(__file__).resolve().parent.parent / "images/node-base/Dockerfile").read_text()
RUNTIME = DOCKERFILE.split("FROM ubuntu:24.04")[-1]
PACKAGES = ("iputils-ping", "iproute2", "dnsutils", "netcat-openbsd", "traceroute", "tcpdump", "net-tools", "curl", "procps", "lsof", "strace", "vim-tiny")


def test_the_runtime_stage_installs_the_tools_a_bare_metal_machine_has_in_its_one_apt_layer():
    assert RUNTIME.count("apt-get install") == 1  # one layer (ADR 0014 addendum)
    for pkg in PACKAGES:
        assert pkg in RUNTIME.split("apt-get install")[1].split("&&")[0], pkg


def test_dcgmi_comes_from_the_exporter_image_the_dcgm_part_uses_and_not_from_apt():
    import re
    tag = lambda text: re.search(r"nvcr\.io/nvidia/k8s/dcgm-exporter:(\S+)", text)[1]
    exporter = (Path(__file__).resolve().parent.parent / "components/dcgm-exporter/Dockerfile").read_text()
    assert tag(DOCKERFILE) == tag(exporter)
    assert "COPY --from=dcgm /usr/bin/dcgmi" in DOCKERFILE and "libdcgm.so" in DOCKERFILE and "dcgm" not in RUNTIME.split("apt-get install")[1].split("&&")[0]
    wrapper = (Path(__file__).resolve().parent.parent / "images/node-base/dcgmi").read_text()
    assert "WORKBENCH_DCGM_HOST" in wrapper and wrapper.startswith("#!/bin/sh")


def test_slurm_is_built_on_every_core_with_a_compiler_cache_and_stays_pinned():
    """#448: make -j2 and no cache made a cold build slow and every Slurm bump a full recompile. The pin (ADR 0002) must survive."""
    build = DOCKERFILE.split("FROM ubuntu:24.04 AS build")[1].split("\nFROM ubuntu:24.04\n")[0]
    assert 'make -j"${BUILD_JOBS:-$(nproc)}"' in build and "-j2" not in build
    assert "--mount=type=cache,target=/ccache" in build and "ccache" in build.split("apt-get install")[1].split("&&")[0]
    assert 'CC="ccache gcc"' in build
    assert "sha256sum -c" in build and "ARG SLURM_SHA256=" in DOCKERFILE and "ARG SLURM_VERSION=" in DOCKERFILE
    assert "/ccache" not in DOCKERFILE.split("\nFROM ubuntu:24.04\n")[1]  # the cache never reaches the image
