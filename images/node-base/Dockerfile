# Slurm built from source: Ubuntu 24.04's package is 23.11, which predates the Aug-Sep 2026 security
# fixes (CVE-2026-65107 and related, fixed in 26.05.4+) and cannot run slurmd in a cgroup namespace
# reliably (24.11+). Pinned version and checksum; bump both together.
ARG SLURM_VERSION=26.05.5
ARG SLURM_SHA256=4680cafc82b0689865c885eb703441e7ebf764896c40fd03a50f2fcb60664f68

# The DCGM command-line client (issue 443): copied from the exporter image the dcgm part uses (keep the tag equal to components/dcgm-exporter/Dockerfile,
# tests/test_node_tools.py), not an apt step. Built on 22.04, it runs on 24.04's newer glibc.
FROM nvcr.io/nvidia/k8s/dcgm-exporter:4.4.2-4.7.0-ubuntu22.04 AS dcgm

FROM ubuntu:24.04 AS build
ARG SLURM_VERSION
ARG SLURM_SHA256
# Parallel jobs for make: every core by default (issue 448; it was two). `docker compose build --build-arg BUILD_JOBS=4 node-base` limits it on a busy machine.
ARG BUILD_JOBS=
# ccache keeps the compiled objects in a BuildKit cache mount: a Slurm bump, or a change to anything above this step, recompiles only what differs.
# NOHASHDIR because the source directory name carries the version; the cache is a build aid only and never reaches the image.
ENV DEBIAN_FRONTEND=noninteractive CCACHE_DIR=/ccache CCACHE_NOHASHDIR=1 CCACHE_MAXSIZE=500M
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ccache ca-certificates curl bzip2 pkg-config python3 \
      libmunge-dev libjson-c-dev libhttp-parser-dev libyaml-dev libjwt-dev libmariadb-dev libdbus-1-dev \
 && rm -rf /var/lib/apt/lists/*
RUN --mount=type=cache,target=/ccache,sharing=locked \
    curl -fsSLo /slurm.tar.bz2 "https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2" \
 && echo "${SLURM_SHA256}  /slurm.tar.bz2" | sha256sum -c - \
 && tar -xjf /slurm.tar.bz2 -C /tmp \
 && cd /tmp/slurm-${SLURM_VERSION} \
 && CC="ccache gcc" ./configure --prefix=/opt/slurm --sysconfdir=/etc/slurm --with-jwt --disable-dependency-tracking >/dev/null \
 && make -j"${BUILD_JOBS:-$(nproc)}" >/dev/null && make install >/dev/null \
 && test -f /opt/slurm/lib/slurm/cgroup_v2.so

# Shared base for every Slurm node (slurmd) and the controller image. The munge key is baked in:
# the sim is a closed lab network, so all containers share one key by design.
FROM ubuntu:24.04
ARG SLURM_VERSION
ENV DEBIAN_FRONTEND=noninteractive PATH=/opt/slurm/bin:/opt/slurm/sbin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
      munge libjson-c5 libhttp-parser2.9 libyaml-0-2 libjwt2 libmariadb3 libdbus-1-3 openssh-server python3 bash-completion less \
      iputils-ping iputils-tracepath iproute2 dnsutils netcat-openbsd traceroute tcpdump net-tools curl wget openssh-client \
      procps psmisc lsof strace file vim-tiny nano \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd \
 && sed -i 's#^SHELL=.*#SHELL=/bin/bash#' /etc/default/useradd \
 && printf '%s\n' 'export PATH=/opt/slurm/bin:$PATH' > /etc/profile.d/slurm.sh \
 && useradd --system --no-create-home slurm \
 && groupadd --system slurmjwt \
 && useradd --system --no-create-home --groups slurmjwt slurmrestd \
 && usermod -aG slurmjwt slurm \
 && mkdir -p /etc/slurm /var/spool/slurmd /var/spool/slurmctld /run/munge /var/log/munge \
 && chown slurm:slurm /var/spool/slurmctld \
 && dd if=/dev/urandom bs=32 count=1 of=/var/spool/slurmctld/jwt_hs256.key 2>/dev/null \
 && chown slurm:slurmjwt /var/spool/slurmctld/jwt_hs256.key && chmod 0640 /var/spool/slurmctld/jwt_hs256.key \
 && dd if=/dev/urandom bs=1 count=1024 of=/etc/munge/munge.key 2>/dev/null \
 && chown -R munge:munge /etc/munge /var/lib/munge /var/log/munge /run/munge \
 && chmod 0400 /etc/munge/munge.key
COPY --from=build /opt/slurm /opt/slurm
# Tab completion for squeue, sbatch, srun, scontrol ... (shipped in the Slurm source tree)
COPY --from=build /tmp/slurm-${SLURM_VERSION}/contribs/slurm_completion_help/slurm_completion.sh /etc/bash_completion.d/slurm
# sshd gives non-interactive sessions a minimal PATH, so the Slurm tools are linked into /usr/local/bin.
RUN echo /opt/slurm/lib >/etc/ld.so.conf.d/slurm.conf && ldconfig \
 && for f in /opt/slurm/bin/*; do ln -sf "$f" /usr/local/bin/; done
# dcgmi (the client only, 5 MB) and its library; the wrapper points it at the node's exporter when it has one (SPEC C-22, C-23)
COPY --from=dcgm /usr/bin/dcgmi /opt/dcgm/dcgmi
COPY --from=dcgm /usr/lib/x86_64-linux-gnu/libdcgm.so.4.4.2 /usr/lib/x86_64-linux-gnu/libdcgm.so.4
COPY --chmod=755 dcgmi /usr/local/bin/dcgmi
COPY sshd_config /etc/ssh/sshd_config
COPY --chmod=755 nhc nhc-prolog nhc-epilog /usr/local/sbin/
COPY --chmod=755 teleport-ssh /usr/local/sbin/teleport-ssh
COPY --chmod=755 slurmd-run /usr/local/sbin/slurmd-run
# The checks the runner uses when the host mounts none (/etc/workbench/nhc, from compose/generated/nhc, wins).
COPY nhc.d/checks.conf nhc.d/*.sh /usr/local/lib/nhc.d/
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["/usr/local/sbin/slurmd-run"]
