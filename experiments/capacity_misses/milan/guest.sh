#!/bin/bash
set -euo pipefail
phase=${1:?primary or repeat}
case "$phase" in primary|repeat) ;; *) exit 2;; esac
test "$#" = 1
test ! -e output
mkdir output
trace=/sys/kernel/tracing
if ! mountpoint -q "$trace"; then mount -t tracefs tracefs "$trace"; fi
instance="$trace/instances/durante-capacity-milan"
mkdir "$instance"
cleanup() {
    echo 0 > "$instance/tracing_on"
    echo 0 > "$instance/events/sched/sched_process_exec/enable"
    rmdir "$instance"
}
trap cleanup EXIT
echo 0 > "$instance/tracing_on"
echo 4096 > "$instance/buffer_size_kb"
echo 1 > "$instance/events/sched/sched_process_exec/enable"
echo 1 > "$instance/tracing_on"
systemd-run --quiet --wait --pipe --collect -p "WorkingDirectory=$PWD" -p AllowedCPUs=1 \
    -p PrivateNetwork=yes -p RuntimeMaxSec=1500 taskset -c 1 setarch x86_64 -R \
    /usr/bin/python3 -B "$PWD/guest.py" "$phase"
echo 0 > "$instance/tracing_on"
cat "$instance/trace" > output/exec-trace.txt
