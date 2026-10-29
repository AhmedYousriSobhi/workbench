# Login node for SSH-based clients (SPEC C-14): sshd plus the Slurm client tools, on the shared node-base.
FROM hpcsim/node-base:dev
# openssh-server (already in node-base) pulls in openssh-client, which the login user needs for `ssh <node>`.
# The Slurm tools are linked into /usr/local/bin in node-base (sshd gives non-interactive sessions a minimal PATH).
COPY sshd_config /etc/ssh/sshd_config
COPY login.sh /login.sh
CMD ["sh", "/login.sh"]
