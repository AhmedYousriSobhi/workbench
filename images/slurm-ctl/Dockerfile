FROM hpcsim/node-base:dev
RUN apt-get update \
 && apt-get install -y --no-install-recommends slurmctld slurmrestd \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /var/spool/slurmctld && chown slurm:slurm /var/spool/slurmctld
CMD ["slurmctld", "-D"]
