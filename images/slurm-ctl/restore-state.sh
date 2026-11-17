#!/bin/sh
# Config lab A/B restore of the slurmctld state (issue 554, ADR 0050). Runs before slurmctld in the container's command.
# Nothing to do unless benchctl/lab_snapshot.py copied a snapshot in while the container was stopped. Then: empty the spool
# (the JWT key stays: the entrypoint installed it from the keys `benchctl up` made, issue 465), extract the snapshot, write the
# sha256 of every file for the host to compare, and remove the tar last, so the host knows the sums belong to this restore.
# The paths can be overridden only so the unit test (tests/test_lab_snapshot.py) can run it in a temporary folder.
set -eu
spool=${WORKBENCH_SPOOL:-/var/spool/slurmctld}
tarf=${WORKBENCH_RESTORE_TAR:-/var/spool/slurmctld.restore.tar}
sums=${WORKBENCH_RESTORED_SUMS:-/var/spool/slurmctld.restored.sha256}
[ -f "$tarf" ] || exit 0
rm -f "$sums"
find "$spool" -mindepth 1 -maxdepth 1 ! -name jwt_hs256.key -exec rm -rf {} +
tar -xf "$tarf" -C "$spool"
if [ "$(id -u)" = 0 ]; then
  find "$spool" -mindepth 1 ! -name jwt_hs256.key -exec chown -h slurm:slurm {} +
fi
(cd "$spool" && find . -type f ! -path ./jwt_hs256.key -exec sha256sum {} +) > "$sums.tmp"
mv "$sums.tmp" "$sums"
rm -f "$tarf"
