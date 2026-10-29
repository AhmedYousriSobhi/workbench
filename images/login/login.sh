#!/bin/sh
# Runs after node-base's entrypoint (simulated users exist, munge is up). Keys come from the host's
# compose/generated/secrets/ssh, mounted read-only; sshd insists the host key is root-owned, so install copies.
set -e
names=""
for u in $HPCSIM_USERS; do
  usermod -p '*' "${u%%:*}"  # unlocked for key login, still no password
  names="$names ${u%%:*}"
done
echo "AllowUsers$names" >> /etc/ssh/sshd_config
install -m 600 /run/hpcsim/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
install -m 644 /run/hpcsim/ssh/authorized_keys /etc/ssh/authorized_keys
exec /usr/sbin/sshd -D -e
