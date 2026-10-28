#!/usr/bin/env bash
# generic/ubuntu2204 resolves names through systemd-resolved, which fails here (IPv6 answers
# first, no IPv6 route): use IPv4 only and a plain resolv.conf. (Learned in hgate-k8slab.)
set -eu
sysctl -qw net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1
printf 'net.ipv6.conf.all.disable_ipv6=1\nnet.ipv6.conf.default.disable_ipv6=1\n' > /etc/sysctl.d/99-sim-ipv6.conf
systemctl disable --now systemd-resolved >/dev/null 2>&1 || true
GW=$(ip -4 route show default | awk '{print $3; exit}')
rm -f /etc/resolv.conf
printf 'nameserver %s\nnameserver 1.1.1.1\n' "$GW" > /etc/resolv.conf
