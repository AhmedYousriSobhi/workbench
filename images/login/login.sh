#!/bin/sh
# Runs after node-base's entrypoint (simulated users exist, munge is up). Keys come from the host's
# compose/generated/secrets/ssh, mounted read-only; sshd insists the host key is root-owned, so install copies.
set -e
names=""
for u in $WORKBENCH_USERS; do
  usermod -p '*' "${u%%:*}"  # unlocked for key login, still no password
  # The cluster key in each user's home, as a shared home directory gives on a real cluster: `ssh cn001` just works.
  install -d -m 700 -o "${u%%:*}" -g "${u%%:*}" "/home/${u%%:*}/.ssh"
  install -m 600 -o "${u%%:*}" -g "${u%%:*}" /run/workbench/ssh/id_cluster "/home/${u%%:*}/.ssh/id_ed25519"
  names="$names ${u%%:*}"
done
echo "AllowUsers$names" >> /etc/ssh/sshd_config
# The nodes share one host key; trusting it for every node name keeps the hop free of prompts.
echo "cn*,gpu* $(cut -d' ' -f1,2 /run/workbench/ssh/ssh_host_node_ed25519_key.pub)" > /etc/ssh/ssh_known_hosts
install -m 600 /run/workbench/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
# WORKBENCH_LOGIN_AUTH (ADR 0023): `teleport` takes no key at all, so the Teleport CA (teleport-ssh below) is the only way in
if [ "${WORKBENCH_LOGIN_AUTH:-ssh}" = "teleport" ]; then
  install -m 644 /dev/null /etc/ssh/authorized_keys
else
  install -m 644 /run/workbench/ssh/authorized_keys /etc/ssh/authorized_keys
fi
/usr/local/sbin/teleport-ssh
exec /usr/sbin/sshd -D -e
