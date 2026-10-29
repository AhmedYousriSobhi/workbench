# Slurm built from source: Ubuntu 24.04's package is 23.11, which predates the Aug-Sep 2026 security
# fixes (CVE-2026-65107 and related, fixed in 26.05.4+) and cannot run slurmd in a cgroup namespace
# reliably (24.11+). Pinned version and checksum; bump both together.
ARG SLURM_VERSION=26.05.5
ARG SLURM_SHA256=4680cafc82b0689865c885eb703441e7ebf764896c40fd03a50f2fcb60664f68

FROM ubuntu:24.04 AS build
ARG SLURM_VERSION
ARG SLURM_SHA256
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl bzip2 pkg-config python3 \
      libmunge-dev libjson-c-dev libhttp-parser-dev libyaml-dev libjwt-dev libmariadb-dev libdbus-1-dev \
 && rm -rf /var/lib/apt/lists/*
RUN curl -fsSLo /slurm.tar.bz2 "https://download.schedmd.com/slurm/slurm-${SLURM_VERSION}.tar.bz2" \
 && echo "${SLURM_SHA256}  /slurm.tar.bz2" | sha256sum -c - \
 && tar -xjf /slurm.tar.bz2 -C /tmp \
 && cd /tmp/slurm-${SLURM_VERSION} \
 && ./configure --prefix=/opt/slurm --sysconfdir=/etc/slurm --with-jwt --disable-dependency-tracking >/dev/null \
 && make -j2 >/dev/null && make install >/dev/null \
 && test -f /opt/slurm/lib/slurm/cgroup_v2.so

# Shared base for every Slurm node (slurmd) and the controller image. The munge key is baked in:
# the sim is a closed lab network, so all containers share one key by design.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive PATH=/opt/slurm/bin:/opt/slurm/sbin:$PATH
RUN apt-get update && apt-get install -y --no-install-recommends \
      munge libjson-c5 libhttp-parser2.9 libyaml-0-2 libjwt2 libmariadb3 libdbus-1-3 openssh-server \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd \
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
# sshd gives non-interactive sessions a minimal PATH, so the Slurm tools are linked into /usr/local/bin.
RUN echo /opt/slurm/lib >/etc/ld.so.conf.d/slurm.conf && ldconfig \
 && for f in /opt/slurm/bin/*; do ln -sf "$f" /usr/local/bin/; done
COPY sshd_config /etc/ssh/sshd_config
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["slurmd", "-D"]
