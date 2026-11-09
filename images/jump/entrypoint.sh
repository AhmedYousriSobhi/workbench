#!/bin/sh
# The same simulated users as the cluster (name:uid ...), key login with the host's keys (mounted read-only), then sshd.
set -e
names=""
for u in $WORKBENCH_USERS; do
  id "${u%%:*}" >/dev/null 2>&1 || useradd -m -u "${u##*:}" -s /bin/bash "${u%%:*}"
  usermod -p '*' "${u%%:*}"
  names="$names ${u%%:*}"
done
echo "AllowUsers$names" >> /etc/ssh/sshd_config
install -m 600 /run/workbench/ssh/ssh_host_jump_ed25519_key /etc/ssh/ssh_host_ed25519_key
install -m 644 /run/workbench/ssh/authorized_keys /etc/ssh/authorized_keys
exec /usr/sbin/sshd -D -e
