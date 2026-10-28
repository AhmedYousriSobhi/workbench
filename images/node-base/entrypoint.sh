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
exec "$@"
