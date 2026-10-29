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
# Compute nodes (HPCSIM_SSHD=1) also run sshd for `ssh <node>` hops and ProxyJump; keys come from the host's
# compose/generated/secrets/ssh, mounted read-only (sshd wants a root-owned host key, so install copies).
if [ "$HPCSIM_SSHD" = 1 ] && [ -f /run/hpcsim/ssh/ssh_host_node_ed25519_key ]; then
  names=""
  for u in $HPCSIM_USERS; do
    usermod -p '*' "${u%%:*}"
    names="$names ${u%%:*}"
  done
  echo "AllowUsers$names" >> /etc/ssh/sshd_config
  install -m 600 /run/hpcsim/ssh/ssh_host_node_ed25519_key /etc/ssh/ssh_host_ed25519_key
  install -m 644 /run/hpcsim/ssh/authorized_keys /etc/ssh/authorized_keys
  /usr/sbin/sshd -e
fi
exec "$@"
