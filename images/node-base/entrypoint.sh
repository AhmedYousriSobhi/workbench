#!/bin/sh
# Fake GPUs: Slurm ignores file-less GRES, so give each GPU a stand-in device file (a symlink
# to /dev/null; no capability needed). Create the simulated users, start munge, exec the daemon.
i=0
while [ "$i" -lt "${HPCSIM_GPUS:-0}" ]; do
  ln -sf /dev/null "/dev/hpcsim-gpu$i"
  i=$((i + 1))
done
# Simulated users (name:uid ...) must resolve identically on every Slurm container.
for u in $HPCSIM_USERS; do
  id "${u%%:*}" >/dev/null 2>&1 || useradd -m -u "${u##*:}" "${u%%:*}"
done
su -s /bin/sh munge -c "munged --force"
# sshd for `ssh <node>` hops from the login node: one host key shared by every node, the client key authorised.
# It daemonizes (pid in /run/sshd.pid); slurmd stays PID 1. sshd insists the host key is root-owned, so install copies.
if [ -n "$HPCSIM_NODE_SSHD" ] && [ -f /run/hpcsim/ssh/ssh_node_host_ed25519_key ]; then
  names=""
  for u in $HPCSIM_USERS; do
    usermod -p '*' "${u%%:*}"  # unlocked for key login, still no password
    names="$names ${u%%:*}"
  done
  echo "AllowUsers$names" >> /etc/ssh/sshd_config
  install -m 600 /run/hpcsim/ssh/ssh_node_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
  install -m 644 /run/hpcsim/ssh/ssh_node_host_ed25519_key.pub /etc/ssh/ssh_host_ed25519_key.pub
  install -m 644 /run/hpcsim/ssh/authorized_keys /etc/ssh/authorized_keys
  /usr/sbin/sshd
fi
exec "$@"
