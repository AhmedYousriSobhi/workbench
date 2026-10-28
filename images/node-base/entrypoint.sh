#!/bin/sh
# Start munge, then exec the requested Slurm daemon in the foreground.
set -e
gosu_munge() { su -s /bin/sh munge -c "$*"; }
gosu_munge "munged --force" || munged --force
exec "$@"
