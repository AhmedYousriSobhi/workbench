#!/bin/sh
# Fake GPUs: Slurm ignores file-less GRES, so give each GPU a stand-in device file (a symlink
# to /dev/null; no capability needed). Create the simulated users, start munge, exec the daemon.
i=0
while [ "$i" -lt "${WORKBENCH_GRES_DEVICES:-${WORKBENCH_GPUS:-0}}" ]; do  # MIG-style nodes have one file per slice (issue 546)
  ln -sf /dev/null "/dev/workbench-gpu$i"
  i=$((i + 1))
done
# slurmd runs the health check, prolog and epilog with a bare environment, so what nhc must expect of this node
# (GPU count, InfiniBand ports) is written to a file it reads.
printf 'WORKBENCH_GPUS=%s\nWORKBENCH_IB=%s\n' "${WORKBENCH_GPUS:-0}" "${WORKBENCH_IB:-0}" > /etc/workbench-node.env
# a node whose card is real (rgpu001) is not in the farm: nhc reads its own exporter instead (issue 437)
[ -n "${WORKBENCH_NHC_URL:-}" ] && printf 'NHC_METRICS_URL=%s\n' "$WORKBENCH_NHC_URL" >> /etc/workbench-node.env
# Simulated users (name:uid ...) must resolve identically on every Slurm container.
for u in $WORKBENCH_USERS; do
  id "${u%%:*}" >/dev/null 2>&1 || useradd -m -u "${u##*:}" "${u%%:*}" || [ -d "/home/${u%%:*}" ]  # a shared /home already holds the directory
done
# A shared mount (issue 461) holds one directory per user, made once and owned by the user, for datasets and job output.
for d in $WORKBENCH_SHARED_MOUNTS; do
  [ "$d" = /home ] && continue
  for u in $WORKBENCH_USERS; do
    install -d -m 750 -o "${u%%:*}" -g "${u%%:*}" "$d/${u%%:*}"
  done
done
# The munge and JWT keys are made by `benchctl up` and mounted read-only; none is baked into the image (issue 465).
if [ ! -f /run/workbench/slurm/munge.key ] || [ ! -f /run/workbench/slurm/jwt_hs256.key ]; then
  echo "workbench: the Slurm keys are not mounted at /run/workbench/slurm; start the cluster with ./scripts/benchctl up" >&2
  exit 1
fi
install -o munge -g munge -m 0400 /run/workbench/slurm/munge.key /etc/munge/munge.key
install -o slurm -g slurmjwt -m 0640 /run/workbench/slurm/jwt_hs256.key /var/spool/slurmctld/jwt_hs256.key
su -s /bin/sh munge -c "munged --force"
# Compute nodes (WORKBENCH_SSHD=1) also run sshd for `ssh <node>` hops and ProxyJump; keys come from the host's
# compose/generated/secrets/ssh, mounted read-only (sshd wants a root-owned host key, so install copies).
if [ "$WORKBENCH_SSHD" = 1 ] && [ -f /run/workbench/ssh/ssh_host_node_ed25519_key ]; then
  names=""
  for u in $WORKBENCH_USERS; do
    usermod -p '*' "${u%%:*}"
    names="$names ${u%%:*}"
  done
  # the controller (issue 416) lets only the whitelisted users in; every other user still exists for Slurm
  [ -n "$WORKBENCH_SSH_ALLOW" ] && names=" $WORKBENCH_SSH_ALLOW"
  echo "AllowUsers$names" >> /etc/ssh/sshd_config
  install -m 600 /run/workbench/ssh/ssh_host_node_ed25519_key /etc/ssh/ssh_host_ed25519_key
  install -m 644 /run/workbench/ssh/authorized_keys /etc/ssh/authorized_keys
  /usr/local/sbin/teleport-ssh
  /usr/sbin/sshd -e
fi
exec "$@"
