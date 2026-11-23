#!/bin/bash
set -euo pipefail
umask 077
cd "$(dirname "$0")"
sha256sum -c checksums.sha256 --quiet
tr=/sys/kernel/tracing
if ! mountpoint -q "$tr"; then mount -t tracefs tracefs "$tr"; fi
inst="$tr/instances/durante_pr"
mkdir "$inst"
echo 0 > "$inst/tracing_on"
echo 4096 > "$inst/buffer_size_kb"
echo 1 > "$inst/events/sched/sched_process_exec/enable"
echo > "$inst/trace"
echo 1 > "$inst/tracing_on"
result=0
systemd-run --quiet --wait --pipe -p AllowedCPUs=1 -p RuntimeMaxSec=2000 --unit=durante-pr-verify /usr/bin/taskset -c 1 /usr/bin/python3 -B "$PWD/bench.py" || result=$?
mkdir -p output
echo 0 > "$inst/tracing_on"
cat "$inst/trace" > output/exec-trace
for f in "$inst"/per_cpu/cpu*/stats; do cat "$f"; done > output/trace-stats
echo 0 > "$inst/events/sched/sched_process_exec/enable"
rmdir "$inst"
exit "$result"
