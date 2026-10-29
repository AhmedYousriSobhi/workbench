# Login node for SSH-based clients (SPEC C-14): sshd plus the Slurm client tools, on the shared node-base.
FROM hpcsim/node-base:dev
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /run/sshd \
 && for f in /opt/slurm/bin/*; do ln -sf "$f" /usr/local/bin/; done
# sshd gives non-interactive sessions a minimal PATH, so the Slurm tools are linked into /usr/local/bin.
COPY sshd_config /etc/ssh/sshd_config
COPY login.sh /login.sh
CMD ["sh", "/login.sh"]
