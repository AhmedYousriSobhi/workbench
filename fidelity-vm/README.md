# Fidelity VM

The single real VM (libvirt/KVM, Ubuntu 22.04, 2 vCPU, 2 GB hard cap), for what containers cannot prove: real kernel counters, systemd, sshd, real `node_exporter`. On demand only; the governor treats it as P6.

    ./bin/up.sh          # memory pre-check, then vagrant up with retry (up to 4 attempts)
    ./bin/down.sh        # destroy
    ./bin/down.sh --save # managedsave: frees its RAM, resumable

Needs `vagrant` with the `vagrant-libvirt` plugin. Networking is DHCP only on purpose; the retry loop exists because vagrant-libvirt's IP detection fails intermittently (see `docs/legacy-lab-reuse.md`). Guest DNS is forced to IPv4 in `provision/common.sh`.

Not yet verified against a real libvirt host: scripts pass `bash -n` only.
