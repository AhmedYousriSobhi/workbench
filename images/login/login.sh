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
  # The fixture kubeconfigs a client's Discover step must judge (issue 767, benchctl/kubeconfigs.py): loose, foreign-owned, root-owned,
  # an exec plugin and a browser sign-in, next to `config`, never replacing it. Each line of the manifest is: path owner group mode.
  kc=/run/workbench/k8s/kubeconfigs
  if [ -r "$kc/manifest" ]; then
    me="${u%%:*}"; other=""
    for o in $WORKBENCH_USERS; do
      if [ -z "$other" ] && [ "${o%%:*}" != "$me" ]; then other="${o%%:*}"; fi
    done
    install -d -m 700 -o "$me" -g "$me" "/home/$me/.kube" "/home/$me/.kube/plugins"
    while read -r path owner group mode; do
      [ -n "$path" ] || continue
      case "$owner" in user) o="$me" ;; other) o="$other" ;; *) o="$owner" ;; esac
      case "$group" in user) g="$me" ;; other) g="$other" ;; *) g="$group" ;; esac
      [ -n "$o" ] && [ -n "$g" ] || continue   # a single simulated user has no "other" user
      sed "s#@HOME@#/home/$me#g" "$kc/$path" > /tmp/kubeconfig-fixture
      install -m "$mode" -o "$o" -g "$g" /tmp/kubeconfig-fixture "/home/$me/.kube/$path"
    done < "$kc/manifest"
    rm -f /tmp/kubeconfig-fixture
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
if [ "${WORKBENCH_HEAD:-1}" = 1 ]; then
  # the head node's CMDaemon events log (issue 714): bcm-mock's state changes as log lines, for a client's history view
  /usr/local/bin/events-log &
else
  rm -f /usr/local/bin/cmsh /usr/local/bin/events-log
fi
/usr/local/sbin/teleport-ssh
# The session counter (issue 213, ADR 0088): sshd logs to a file, the counter follows it, serves the count on :9922 and repeats each line to
# `docker logs`. Compose mounts it; without it (an older mount) sshd still runs, only the diagram's SSH dots are missing.
: > /var/log/sshd.log
if [ -x /usr/local/bin/ssh-counter ]; then /usr/local/bin/ssh-counter 9922 /var/log/sshd.log & fi
exec /usr/sbin/sshd -D -E /var/log/sshd.log
