#!/bin/bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")" && pwd)
test "$#" = 0
test ! -e "$root/output"
# The pinned pilot bootstrap mounts the runtime read-only and launches guest.py
# in a private network namespace and a CPU1-only systemd scope.
driver=$(mktemp -d /var/tmp/durante-qualified-features-XXXXXXXX)
trap 'rm -rf -- "$driver"' EXIT
chmod 0755 "$driver"
ln -s "$root/guest.py" "$driver/guest.py"
ln -s "$root/pilot/runtime.squashfs" "$driver/runtime.squashfs"
ln -s "$root/pilot/guest.sh" "$driver/bootstrap.sh"
bash "$driver/bootstrap.sh" calibrate
