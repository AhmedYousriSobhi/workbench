# Jump host (SPEC C-21): sshd and nothing else, used with `ssh -J` to reach a login node or the controller.
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends openssh-server \
 && rm -rf /var/lib/apt/lists/* && mkdir -p /run/sshd
COPY sshd_config /etc/ssh/sshd_config
COPY --chmod=755 entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
