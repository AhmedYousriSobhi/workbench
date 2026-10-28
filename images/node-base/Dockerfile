# Shared base for every Slurm node (slurmd) and the controller image. Munge key is baked in:
# the sim is a closed lab network, so all containers share one key by design.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends munge slurmd slurm-client \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/spool/slurmd /run/munge /var/log/munge \
 && dd if=/dev/urandom bs=1 count=1024 of=/etc/munge/munge.key 2>/dev/null \
 && chown -R munge:munge /etc/munge /var/lib/munge /var/log/munge /run/munge \
 && chmod 0400 /etc/munge/munge.key
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["slurmd", "-D"]
