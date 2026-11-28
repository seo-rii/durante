"""Decode an original or five-feature diagnostic alongside an authenticated process report."""
import re

KEEP = ("latency_x16", "rep_bytes", "l1d_dependent", "dependent_2m", "stream_2m")
SMALL = re.compile(rb"PROBE_ML4_FIVE capacity_lines=32768 l1d_dependent=(\d+) dependent_2m=(\d+) "
                   rb"stream_2m=(\d+) skipped=(\d+) threads=1\n")


def counters(arm, report, stderr, capacity):
    if not (report["valid"] and report["complete"] and report["quality"] == "uninterrupted"
            and report["process_count"] == report["image_count"] == report["segments"][0]["threads"] == 1
            and report["signals_observed"] == 0 and report["cpuid_profile"] == "x86-64-v2"
            and report["processes"][0]["exit_code"] == 0 and report["rep_telemetry_status"] == "movs_stos_only"):
        raise ValueError("ineligible process report")
    if arm == "reduced":
        match = SMALL.fullmatch(stderr)
        if not match:
            raise ValueError("unexpected five-feature diagnostic")
        l1dep, dep, stream, skipped = map(int, match.groups())
    elif arm == "full":
        first, second = stderr.splitlines()
        l1 = capacity["PROBE_L1D"].fullmatch(first)
        outer = capacity["PROBE_OUTER"].fullmatch(second)
        if not l1 or not outer:
            raise ValueError("unexpected full diagnostic")
        o = [int(v) for v in outer.groups()]
        if o[:3] != [4096, 32768, 524288]:
            raise ValueError("different full cache hierarchy")
        l1dep, dep, stream, skipped = o[13], o[11], o[8], int(l1.group(4))
    else:
        raise ValueError("unknown probe")
    return dict(zip(KEEP, (report["latency_bound"]["cycles_x16"], report["rep_bytes"], l1dep, dep, stream))), skipped
