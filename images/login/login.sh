#!/bin/sh
# Runs after node-base's entrypoint (simulated users exist, munge is up). Keys come from the host's
# compose/generated/secrets/ssh, mounted read-only; sshd insists the host key is root-owned, so install copies.
set -e
names=""
for u in $HPCSIM_USERS; do
  usermod -p '*' "${u%%:*}"  # unlocked for key login, still no password
  names="$names ${u%%:*}"
  # The login user's own key for `ssh <node>` hops (a client that runs Slurm on another host does this).
  install -d -m 700 -o "${u%%:*}" -g "${u%%:*}" "/home/${u%%:*}/.ssh"
  install -m 600 -o "${u%%:*}" -g "${u%%:*}" /run/hpcsim/ssh/id_ed25519 "/home/${u%%:*}/.ssh/id_ed25519"
done
# Nodes share one host key; trust it for every node name so the first hop does not stop at a prompt.
if [ -n "$HPCSIM_NODES" ]; then
  echo "$(echo $HPCSIM_NODES | tr ' ' ',') $(cut -d' ' -f1,2 /run/hpcsim/ssh/ssh_node_host_ed25519_key.pub)" > /etc/ssh/ssh_known_hosts
fi
echo "AllowUsers$names" >> /etc/ssh/sshd_config
install -m 600 /run/hpcsim/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
install -m 644 /run/hpcsim/ssh/ssh_host_ed25519_key.pub /etc/ssh/ssh_host_ed25519_key.pub  # replaces the package's own, which sshd warns about
install -m 644 /run/hpcsim/ssh/authorized_keys /etc/ssh/authorized_keys
exec /usr/sbin/sshd -D -e
