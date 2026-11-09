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
  # Kubernetes (ADR 0034), when the part is on: the read-only kubeconfig where kubectl and a client's Discover step look,
  # owned by the user and mode 600 as a real admin hand-over is; `limited` sees only namespace team-a
  if [ -r /run/workbench/k8s/viewer.kubeconfig ]; then
    install -d -m 700 -o "${u%%:*}" -g "${u%%:*}" "/home/${u%%:*}/.kube"
    install -m 600 -o "${u%%:*}" -g "${u%%:*}" /run/workbench/k8s/viewer.kubeconfig "/home/${u%%:*}/.kube/config"
    install -m 600 -o "${u%%:*}" -g "${u%%:*}" /run/workbench/k8s/limited.kubeconfig "/home/${u%%:*}/.kube/limited.yaml"
  fi
  names="$names ${u%%:*}"
done
echo "AllowUsers$names" >> /etc/ssh/sshd_config
# The nodes share one host key; trusting it for every node name keeps the hop free of prompts.
echo "cn*,gpu*,slurmctld,controller $(cut -d' ' -f1,2 /run/workbench/ssh/ssh_host_node_ed25519_key.pub)" > /etc/ssh/ssh_known_hosts
install -m 600 /run/workbench/ssh/ssh_host_ed25519_key /etc/ssh/ssh_host_ed25519_key
# WORKBENCH_LOGIN_AUTH (ADR 0023): `teleport` takes no key at all, so the Teleport CA (teleport-ssh below) is the only way in
if [ "${WORKBENCH_LOGIN_AUTH:-ssh}" = "teleport" ]; then
  install -m 644 /dev/null /etc/ssh/authorized_keys
else
  install -m 644 /run/workbench/ssh/authorized_keys /etc/ssh/authorized_keys
fi
# the controller, when there is one, is the BCM head node (issue 416): the login nodes lose cmsh
[ "${WORKBENCH_HEAD:-1}" = 1 ] || rm -f /usr/local/bin/cmsh
/usr/local/sbin/teleport-ssh
exec /usr/sbin/sshd -D -e
