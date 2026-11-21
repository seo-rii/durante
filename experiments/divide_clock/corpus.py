"""Resolve original ELFs, GCE CPU times and production measurements from the published archives."""
import argparse
import gzip
import json
from pathlib import Path
import runpy
import tarfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
RESULTS = HERE.parent / "results"
support = runpy.run_path(str(HERE.parent / "dependency_pairs/study.py"))
require, sha = support["require"], support["sha"]
PRIOR = [
    ("dependency_pairs", "gce-dependency-pairs",
     ("div_small_serial", "div_small_parallel", "div_large_serial", "div_large_parallel")),
    ("dependency_pairs/followup", "gce-dependency-pairs/followup",
     ("div_xor_serial", "div_xor_parallel", "div_mov_serial", "div_mov_parallel")),
    ("access_sample", "gce-access-sample",
     ("integer", "copy", "branch", "sort", "fill", "gcd", "lower_bound", "rolling_window")),
]
TOOLS = ("launcher", "runtime", "meter", "production_reader")


def read_member(archive, name):
    m = archive.getmember(name)
    require(m.isfile() and m.size <= 16*1024*1024, "invalid archive member: " + name)
    return archive.extractfile(m).read()


def corpus(binaries=None):
    cases, inputs, tools = {}, {}, {}
    for study, result, names in PRIOR:
        base = RESULTS / result
        protocol = HERE.parent / study / "protocol.json"
        dataset = base / "dataset.json.gz"
        records = base / ("receipts.tar.xz" if study == "access_sample" else "samples.tar.xz")
        platform = base / "platform.json"
        for path in (protocol, dataset, records, platform, base / "placement.json.gz"):
            inputs[path.relative_to(ROOT).as_posix()] = sha(path.read_bytes())
        plan = json.loads(protocol.read_bytes())
        raw = gzip.decompress(dataset.read_bytes())
        data, host = json.loads(raw), json.loads(platform.read_bytes())
        require(data["complete"] is True and data["cpu"] == host["benchmark_cpu"] == 1 and
                host["dataset_sha256"] == sha(raw) and host["target"] == "compute_engine" and
                host["placement_sha256"] == sha((base / "placement.json.gz").read_bytes()) and
                sha(protocol.read_bytes()) == host["protocol_sha256"] == data["protocol_sha256"], "GCE provenance mismatch")
        previous = {k: data["tools"][k] for k in TOOLS}
        require(all(plan["tool_sha256"][k] == value for k, value in previous.items()), "GCE tools changed")
        require(not tools or tools == previous, "incompatible baseline tools across studies")
        tools = previous
        selected = [c for c in plan["cases"] if c["workload_id"] in names]
        require([c["workload_id"] for c in selected] == list(names), "unexpected workload selection")
        for c in selected:
            name = c["workload_id"]
            archive_path = ROOT/c["archive"] if "archive" in c else records
            rel_archive = archive_path.relative_to(ROOT).as_posix()
            if rel_archive not in inputs:
                inputs[rel_archive] = sha(archive_path.read_bytes())
            with tarfile.open(archive_path) as archive:
                elf = read_member(archive, f"bin/{name}/Main")
            stdin = bytes.fromhex(c.get("stdin_hex", ""))
            stdout = bytes.fromhex(c["stdout_hex"])
            require(elf.startswith(b"\x7fELF") and sha(elf) == c["binary_sha256"] == data["programs"][name]["binary_sha256"]
                    and sha(stdin) == c["input_sha256"] and sha(stdout) == c["expected_sha256"], "fixture mismatch: " + name)
            if binaries:
                dest = binaries/name/"Main"
                dest.parent.mkdir(parents=True, exist_ok=True)
                if dest.exists():
                    require(dest.read_bytes() == elf, "conflicting ELF: " + name)
                else:
                    dest.write_bytes(elf)
                    dest.chmod(0o700)
            cases[name] = {"binary_sha256": sha(elf), "stdin_hex": stdin.hex(), "stdout_hex": stdout.hex(),
                           "native_ns": [], "gce_production": [], "source_dataset": result, "binary_archive": rel_archive}
        seen = {name: set() for name in names}
        with tarfile.open(records) as archive:
            for i, row in enumerate(data["samples"], 1):
                name = row["workload_id"]
                if name not in names:
                    continue
                case = cases[name]
                rep = row["repetition"]
                require(rep not in seen[name] and case["binary_sha256"] == row["binary_sha256"], "duplicate or wrong case")
                seen[name].add(rep)
                folder = f"samples/{i:02d}-{name}-{rep}"
                require(read_member(archive, folder+"/stdin").hex() == case["stdin_hex"], "wrong GCE input")
                for phase in ("native_before", "native_after"):
                    obs = row[phase]
                    wait = json.loads(read_member(archive, f"{folder}/{phase}/phase.json"))
                    require(wait == obs and obs["exit_code"] == 0 and obs["cpu_time_ns"] > 0 and
                            obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"], "invalid GCE native time")
                    stdout = read_member(archive, f"{folder}/{phase}/stdout")
                    require(stdout.hex() == case["stdout_hex"] and sha(stdout) == obs["stdout_sha256"] == row["expected_sha256"],
                            "wrong GCE output or time receipt")
                    case["native_ns"].append(obs["cpu_time_ns"])
                phase = "baseline" if study == "access_sample" else "operand_bytes"
                case["gce_production"].append(row[phase]["production"])
        n = 3 if study == "access_sample" else 4
        require(all(reps == set(range(1, n+1)) for reps in seen.values()), "missing GCE repetitions")
    return {"inputs_sha256": inputs, "tools": tools, "cases": cases}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binaries", type=Path)
    print(json.dumps(corpus(parser.parse_args().binaries), indent=2))
