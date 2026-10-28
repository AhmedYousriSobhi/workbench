#!/bin/sh
# Fake GPUs: Slurm ignores file-less GRES, so give each GPU a stand-in device file (a symlink
# to /dev/null; no capability needed). Then start munge and exec the Slurm daemon.
i=0
while [ "$i" -lt "${HPCSIM_GPUS:-0}" ]; do
  ln -sf /dev/null "/dev/hpcsim-gpu$i"
  i=$((i + 1))
done
su -s /bin/sh munge -c "munged --force"
exec "$@"
