#!/usr/bin/env python3
"""Which work features would a millisecond conversion need?

`collect` runs the fixed corpus in experiments/time_features natively (median
user+system CPU of pinned runs, measured by wait4) and twice more under
DynamoRIO: once with an exploratory feature client (static per-BB instruction
classes charged at BB entry, plus a deterministic gshare branch-predictor
simulation) and once under drcachesim (deterministic simulated L1/LL misses).
Native timing, features and cache simulation use the same input and scale.

`fit` evaluates nonnegative linear models from instruction count alone to
static classes, branch mispredictions and cache misses, by leave-one-workload-
out relative error. It selects no production model: a single host, a small
fixed corpus and simulated microarchitecture are diagnostic evidence only.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time

SCALES = {"int_chain": 3, "int_ilp": 6, "branch_pred": 8, "branch_rand": 2, "branch_rand_cmov": 2,
          "sort_rand": 1, "sort_sorted": 3, "chase_l1": 4, "chase_l2": 2, "chase_dram": 1,
          "seq_read": 2, "fp_chain": 4, "fp_ilp": 5, "div_chain": 5, "fib": 10,
          "hash_insert": 2, "matmul": 2, "bytes": 6}
STATIC = ["instructions", "loads", "stores", "cond_branches", "direct_jumps_calls", "indirect_branches",
          "muls", "divs", "fp_ops"]
MODELS = {
    "M0 instructions": ["instructions"],
    "M1 static classes": STATIC,
    "M2 static + mispredicts": STATIC + ["mispredicts"],
    "M3 static + mispredicts + cache misses": STATIC + ["mispredicts", "l1d_misses", "ll_misses"],
    "M3c compact": ["instructions", "divs", "fp_ops", "mispredicts", "l1d_misses", "ll_misses"],
}


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def native_once(argv, cpu):
    pid = os.fork()
    if pid == 0:
        try:
            os.sched_setaffinity(0, {cpu})
            null = os.open(os.devnull, os.O_WRONLY)
            os.dup2(null, 1)
            os.execv(argv[0], argv)
        finally:
            os._exit(127)
    _, status, usage = os.wait4(pid, 0)
    if status != 0:
        raise SystemExit(f"native run failed: {argv}")
    return int((usage.ru_utime + usage.ru_stime) * 1e9)


def cache_stats(text):
    def section(label):
        block = text.split(label, 1)[1]
        pairs = re.findall(r"^\s+(Hits|Misses):\s+(\d+)", block, re.M)[:2]
        return {key.lower(): int(value) for key, value in pairs}
    return {"l1i": section("L1I0"), "l1d": section("L1D0"), "ll": section("LL (")}


def collect(args):
    args.output_dir.mkdir(mode=0o700)
    dataset_path = args.output_dir / "dataset.json"
    drrun = str(args.sdk / "bin64/drrun")
    meta = {"host": {"cpu_model": next((line.split(":", 1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                                        if line.startswith("model name")), "unknown"),
                     "kernel": platform.release(), "pinned_cpu": args.cpu},
            "tools": {"work_sha256": digest(args.work), "client_sha256": digest(args.client),
                      "drrun_sha256": digest(drrun)},
            "repeats": args.repeats, "workloads": {}}
    for name, scale in SCALES.items():
        argv = [str(args.work.resolve()), name, str(scale)]
        samples = [native_once(argv, args.cpu) for _ in range(args.repeats)]
        median = int(statistics.median(samples))
        fd, feature_path = tempfile.mkstemp(dir=args.output_dir, suffix=".features.json")
        os.close(fd)
        os.unlink(feature_path)
        subprocess.run(["setarch", "-R", drrun, "-quiet", "-disable_traces", "-c", str(args.client.resolve()),
                        "-out", feature_path, "-bpred", "--"] + argv, check=True, capture_output=True)
        record = {"scale": scale, "native_ns": median, "native_samples": samples,
                  "native_spread": (max(samples) - min(samples)) / median,
                  "features": json.loads(Path(feature_path).read_text())}
        if args.cachesim_root:
            simulated = subprocess.run(["setarch", "-R", str(args.cachesim_root / "bin64/drrun"), "-t", "drcachesim", "--"] + argv,
                                       check=True, capture_output=True, text=True).stderr
            record["cache"] = cache_stats(simulated)
        meta["workloads"][name] = record
        dataset_path.write_text(json.dumps(meta, indent=1) + "\n")
        print(name, round(median / 1e6, 1), "ms", flush=True)
    return 0


def nnls(a, b, iterations=500):
    import numpy as np
    n = a.shape[1]
    passive = np.zeros(n, bool)
    x = np.zeros(n)
    gradient = a.T @ (b - a @ x)
    for _ in range(iterations):
        if passive.all() or gradient[~passive].max() <= 1e-12:
            break
        passive[int(np.argmax(np.where(passive, -np.inf, gradient)))] = True
        while True:
            z = np.zeros(n)
            z[passive] = np.linalg.lstsq(a[:, passive], b, rcond=None)[0]
            if (z[passive] > 0).all():
                x = z
                break
            negative = passive & (z <= 0)
            step = np.min(x[negative] / (x[negative] - z[negative]))
            x = x + step * (z - x)
            passive &= x > 1e-15
        gradient = a.T @ (b - a @ x)
    return x


def values(record):
    f = record["features"]
    out = {name: f[name] for name in STATIC}
    out["mispredicts"] = f["mispredicts"]
    if "cache" in record:
        out["l1d_misses"] = record["cache"]["l1d"]["misses"]
        out["ll_misses"] = record["cache"]["ll"]["misses"]
    return out


def fit(args):
    import numpy as np
    data = json.loads(args.input.read_text())
    workloads = data.get("workloads", data)
    keys = sorted(workloads)
    times = np.array([workloads[k]["native_ns"] for k in keys], float)
    evaluation = {"input_sha256": digest(args.input), "workloads": keys, "models": {}}
    for label, names in MODELS.items():
        rows = [values(workloads[k]) for k in keys]
        if any(name not in row for row in rows for name in names):
            continue
        x = np.array([[float(row[name]) for name in names] for row in rows])
        scale = x.max(axis=0)
        scale[scale == 0] = 1
        xs = x / scale
        errors = {}
        for i, key in enumerate(keys):
            train = [j for j in range(len(keys)) if j != i]
            weights = nnls(xs[train] / times[train, None], np.ones(len(train)))
            errors[key] = float((xs[i] @ weights - times[i]) / times[i])
        weights = nnls(xs / times[:, None], np.ones(len(keys))) / scale
        absolute = np.abs(np.array(list(errors.values())))
        evaluation["models"][label] = {
            "features": names, "loo_signed_error": errors,
            "median_abs_error": float(np.median(absolute)), "max_abs_error": float(absolute.max()),
            "within_20_percent": int((absolute <= 0.2).sum()),
            "all_data_ns_per_feature": dict(zip(names, map(float, weights)))}
        print(f"{label:40s} LOO |error| median {np.median(absolute) * 100:5.1f}%  max {absolute.max() * 100:6.1f}%  "
              f"within 20% {int((absolute <= 0.2).sum())}/{len(keys)}")
    if args.output:
        args.output.write_text(json.dumps(evaluation, indent=2) + "\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    c = sub.add_parser("collect")
    c.add_argument("--sdk", type=Path, required=True, help="DynamoRIO root used for the feature client")
    c.add_argument("--client", type=Path, required=True, help="built libtime_features_client.so")
    c.add_argument("--work", type=Path, required=True, help="built time_features_work")
    c.add_argument("--cachesim-root", type=Path, help="full DynamoRIO release root containing tools/drcachesim")
    c.add_argument("--output-dir", type=Path, required=True)
    c.add_argument("--cpu", type=int, default=0)
    c.add_argument("--repeats", type=int, default=9)
    f = sub.add_parser("fit")
    f.add_argument("--input", type=Path, required=True)
    f.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "collect":
        if not 3 <= args.repeats <= 25:
            raise SystemExit("--repeats must be between 3 and 25")
        return collect(args)
    return fit(args)


if __name__ == "__main__":
    sys.exit(main())
