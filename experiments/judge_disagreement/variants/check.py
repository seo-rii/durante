#!/usr/bin/env python3
"""Compile and verify the contest solution variants listed in manifest.json.

For every problem this compiles the intended tests/contest/<problem>/sol.cpp and
each variant with the contest flags, generates the "lite" and "full" inputs with
problems.generate, and checks that every program's stdout is byte-identical to
problems.reference. It also reports a rough CPU time (user+sys, best of --reps)
and the ratio to the intended solution on the full tier, plus peak RSS (exact
above this script's own RSS of roughly 15 MB, which is the floor ru_maxrss shows).

Everything (this script, g++ and the programs) is pinned to one CPU, default 0,
and run one at a time. Programs run with RLIMIT_STACK raised to the hard limit
(deep_dfs recurses 1e6 levels) and RLIMIT_CPU as the timeout.

Usage: python3 check.py [--problems bfs_grid,kmp] [--tiers lite,full]
                        [--reps 3] [--timeout 120] [--cpu 0]
Exit status is 0 only if every program compiled and every output matched.
"""
import argparse
import json
import os
import resource
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DURANTE = os.environ.get("DURANTE_ROOT", os.path.abspath(os.path.join(HERE, "../../..")))
CONTEST = os.path.join(DURANTE, "tests", "contest")
FLAGS = ["-std=c++20", "-O2", "-static", "-no-pie", "-march=x86-64", "-mtune=generic"]
BUILD = os.path.join(HERE, "_build", "check")

sys.dont_write_bytecode = True  # never write __pycache__ into the Durante checkout
sys.path.insert(0, CONTEST)
import problems  # noqa: E402


def load_manifest():
    with open(os.path.join(HERE, "manifest.json")) as f:
        manifest = json.load(f)
    errors = []
    for prob, entry in manifest["problems"].items():
        listed = {os.path.basename(v["path"]) for v in entry["variants"]}
        on_disk = {f for f in os.listdir(os.path.join(HERE, prob)) if f.endswith(".cpp")}
        for extra in sorted(on_disk - listed):
            errors.append(f"{prob}/{extra} is not in manifest.json")
        for missing in sorted(listed - on_disk):
            errors.append(f"{prob}/{missing} is in manifest.json but missing on disk")
    return manifest, errors


def compile_one(src, out):
    os.makedirs(os.path.dirname(out), exist_ok=True)
    r = subprocess.run(["g++", *FLAGS, "-o", out, src], capture_output=True, text=True)
    return r.returncode == 0, r.stderr


def run_one(binary, inp_path, timeout):
    def limits():
        hard = resource.getrlimit(resource.RLIMIT_STACK)[1]
        resource.setrlimit(resource.RLIMIT_STACK, (hard, hard))
        resource.setrlimit(resource.RLIMIT_CPU, (timeout, timeout + 5))

    with open(inp_path, "rb") as f:
        p = subprocess.Popen([binary], stdin=f, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             preexec_fn=limits)
        out = p.stdout.read()
        p.stdout.close()
        _, status, ru = os.wait4(p.pid, 0)
    return out, status, ru.ru_utime + ru.ru_stime, ru.ru_maxrss


def emit(prob, tier, inp, ans):
    data = problems.generate(prob, tier)
    with open(inp, "wb") as f:
        f.write(data)
    with open(ans, "wb") as f:
        f.write(problems.reference(prob, data).encode())


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "--emit":
        emit(*sys.argv[2:])
        return 0
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--problems", default=",".join(problems.PROBLEMS))
    ap.add_argument("--tiers", default="lite,full")
    ap.add_argument("--reps", type=int, default=1, help="runs per program per tier; the best CPU time is kept")
    ap.add_argument("--timeout", type=int, default=120, help="CPU-seconds limit per run")
    ap.add_argument("--cpu", type=int, default=0)
    args = ap.parse_args()
    sys.stdout.reconfigure(line_buffering=True)
    os.sched_setaffinity(0, {args.cpu})  # g++ and every run inherit this

    manifest, errors = load_manifest()
    for e in errors:
        print("MANIFEST:", e)
    tiers = args.tiers.split(",")
    failures = len(errors)
    results = {}
    for prob in args.problems.split(","):
        entry = manifest["problems"][prob]
        programs = [("sol (intended)", os.path.join(DURANTE, entry["intended"]), None)]
        programs += [(os.path.splitext(os.path.basename(v["path"]))[0], os.path.join(DURANTE, v["path"]),
                      v.get("ratio"))
                     for v in entry["variants"]]
        bins = {}
        for name, src, _ in programs:
            out = os.path.join(BUILD, "bin", prob, name.split()[0])
            ok, err = compile_one(src, out)
            if ok:
                bins[name] = out
            else:
                failures += 1
                print(f"COMPILE FAIL {prob}/{name}:\n{err}")
        tier_data = {}
        for tier in tiers:
            inp = os.path.join(BUILD, "inputs", f"{prob}.{tier}.in")
            ans = os.path.join(BUILD, "inputs", f"{prob}.{tier}.ans")
            os.makedirs(os.path.dirname(inp), exist_ok=True)
            # Generate in a child process so this process stays small: Linux carries the
            # forking parent's peak RSS into each program's ru_maxrss across exec.
            subprocess.run([sys.executable, "-B", os.path.abspath(__file__), "--emit", prob, tier, inp, ans],
                           check=True)
            with open(ans, "rb") as f:
                tier_data[tier] = (inp, f.read())
        res = results[prob] = {}
        for name, _, ratio in programs:
            r = res[name] = {"claimed_ratio": ratio}
            if name not in bins:
                r["status"] = "compile-fail"
                continue
            for tier in tiers:
                inp, expected = tier_data[tier]
                best, rss, verdict = None, 0, "OK"
                for _ in range(args.reps):
                    out, status, cpu, maxrss = run_one(bins[name], inp, args.timeout)
                    if status != 0:
                        verdict = f"EXIT {status:#x}"
                    elif out != expected:
                        verdict = "WRONG"
                    best = cpu if best is None else min(best, cpu)
                    rss = max(rss, maxrss)
                    if verdict != "OK":
                        break
                if verdict != "OK":
                    failures += 1
                r[tier] = {"verdict": verdict, "cpu_s": round(best, 4), "maxrss_kb": rss}
        base = res["sol (intended)"].get("full", {}).get("cpu_s")
        print(f"\n== {prob}")
        print(f"   {'program':26s} " + " ".join(f"{t:>6s}" for t in tiers) +
              "   full_cpu  ratio  claimed  maxrss_MB")
        for name, r in res.items():
            if "status" in r:
                print(f"   {name:26s} {r['status']}")
                continue
            verdicts = " ".join(f"{r[t]['verdict']:>6s}" for t in tiers)
            full = r.get("full")
            if full:
                ratio = full["cpu_s"] / base if base else float("nan")
                claimed = f"{r['claimed_ratio']:>7}" if r["claimed_ratio"] is not None else "      -"
                print(f"   {name:26s} {verdicts}   {full['cpu_s']:7.3f}s {ratio:5.2f}x  {claimed}"
                      f"  {full['maxrss_kb'] / 1024:8.1f}")
            else:
                print(f"   {name:26s} {verdicts}")
    with open(os.path.join(BUILD, "results.json"), "w") as f:
        json.dump(results, f, indent=1)
    total = sum(len(r) for r in results.values())
    print(f"\n{'ALL OK' if failures == 0 else f'{failures} FAILURE(S)'}: "
          f"{total} programs x {len(tiers)} tiers checked (results in {os.path.join(BUILD, 'results.json')})")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
