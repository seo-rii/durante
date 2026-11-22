"""Run a trusted deterministic, single-process ELF with first-use calibration and coverage-based full recovery."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import calibration_sites, capture_for_digest, elf_digest, read_json, resolve_for_digest
from guarded_sampler.guard import decision, read_report


def save(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")


def publish_profile(cache_file, profile):
    # Two concurrent first uses may both run Full. Publish one complete profile.
    with tempfile.NamedTemporaryFile(dir=cache_file.parent) as temp:
        temp.write((json.dumps(profile, indent=2) + "\n").encode())
        temp.flush()
        try:
            os.link(temp.name, cache_file)
        except FileExistsError:
            resolve_for_digest(read_json(cache_file.read_bytes()), profile["elf_sha256"])


def execute(runtime, plan, elf, stdin, out, offsets=None):
    out.mkdir()
    argv = runtime.command("full", elf, out)
    if offsets is not None:
        secondary = argv.index(runtime.ROOT / "span.so")
        argv[secondary] = runtime.ROOT / "guarded.so"
        argv[secondary + 2] += " -sites " + offsets + " -guard " + runtime.quoted(out / "guard.json")
    row = runtime.base["execute"](argv, out, stdin, plan)
    runtime.finish_live("full", out, row["launcher_pid"])
    return row


def full_sample(directory, pid):
    raw = (directory / "sample.json").read_bytes()
    sample = read_json(raw)
    if sample["sites"]:
        return raw
    empty = dict(schema=2, pid=pid, threads=1, period=1024, line_bytes=64,
                 loads=0, pairs=0, near_pairs=0, far_pairs=0, sites=[])
    if sample != empty or any(type(sample[k]) is not int for k in empty if k != "sites"):
        raise ValueError("inconsistent empty full sample")
    return None


def run(payload, elf, stdin, cache, out):
    plan_file = HERE / "protocol.json"
    if plan_file.read_bytes() != (payload / "protocol.json").read_bytes():
        raise ValueError("payload protocol differs from the registered study")
    plan = json.loads(plan_file.read_bytes())
    for name, digest in plan["tools_sha256"].items():
        if elf_digest(payload / name) != digest:
            raise ValueError("changed registered payload file: " + name)
    sys.path.insert(0, str(payload))
    import cost_bench as runtime
    signal.signal(signal.SIGALRM, runtime.base["timeout"])

    out.mkdir(mode=0o700)
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    input_copy = out / "stdin"
    input_copy.write_bytes(stdin.read_bytes())
    digest = elf_digest(elf)
    cached = cache / (digest + ".json")
    result = {"elf_sha256": digest, "stdin_sha256": elf_digest(input_copy), "profile": str(cached),
              "authority": "diagnostic_only", "launches": []}
    if not cached.exists():
        row = execute(runtime, plan, elf, input_copy, out / "full")
        raw = full_sample(out / "full", row["launcher_pid"])
        if raw is None:
            result["profile"] = None
        else:
            profile = capture_for_digest(digest, input_copy.read_bytes(), raw, row["launcher_pid"], arm="full")
            publish_profile(cached, profile)
        result["launches"].append({"mode": "full", **row})
        final = "full"
    else:
        offsets = resolve_for_digest(read_json(cached.read_bytes()), digest)
        guarded = out / "guarded"
        row = execute(runtime, plan, elf, input_copy, guarded, offsets)
        result["launches"].append({"mode": "guarded", **row})
        result["decision"] = decision(read_report(guarded / "sample.json"), read_report(guarded / "guard.json"),
                                      row["launcher_pid"])
        final = "guarded"
        if result["decision"]["status"] == "run_full":
            if elf_digest(elf) != digest:
                raise ValueError("executable changed before full recovery")
            rerun = execute(runtime, plan, elf, input_copy, out / "full")
            raw = full_sample(out / "full", rerun["launcher_pid"])
            if raw is not None:
                calibration_sites(raw, rerun["launcher_pid"])
            if rerun["stdout_sha256"] != row["stdout_sha256"]:
                raise ValueError("rerun produced different output for the same input")
            result["launches"].append({"mode": "full", **rerun})
            final = "full"
    result.update(final_sample=final + "/sample.json", stdout=final + "/stdout")
    save(out / "result.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for flag, help_text in (("payload", "verified directory produced by prepare.py"), ("elf", "immutable trusted ELF"),
                            ("stdin", "input file"), ("cache", "private directory for per-ELF profiles"),
                            ("out", "new output directory for this invocation")):
        parser.add_argument("--" + flag, type=Path, required=True, help=help_text)
    args = parser.parse_args()
    print(json.dumps(run(*(getattr(args, k).resolve() for k in ("payload", "elf", "stdin", "cache", "out")))))
