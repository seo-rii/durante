"""Join exact inputs to the archived Aonohako references and replay the delivered Durante telemetry."""
import argparse
import gzip
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.score import hot_site
from cpp20_locality.verify import require, sha
from guarded_sampler import guard, verify as guarded_verify
from guarded_sampler.cases import manifest

GUARDED = HERE.parent / "results/gce-guarded-sampler"
REFERENCE = HERE / "archived_reference.json"
REFERENCE_SHA = "009a96196f46a7456f7948aee1466ae27b98e7a5633f5ea414ffd13e25e08e6e"
JOIN_KEYS = ("case_id", "program", "binary_sha256", "stdin_sha256", "stdout_sha256")
ARCHIVE_SHA = {
    "measurements.json.gz": "1a4448aac68a206896e16746c3776859b1bf66ebb391d623505c42c3b6a05fd0",
    "evaluation.json.gz": "5525cb0be2d5eee45c3dd23db952238544ee77213bf1c1be0d10ec44ef967f22",
    "placement.json.gz": "63dbe0c2cc976e8b9ee4d1060437f4236ab531c484a4b908e09427e0832dfaa9",
    "reports.tar.xz": "f2c114fd8cb27c27c4a07b2da6086ae28b314cb47d56c212ca37db467bc4f154",
    "corpus/elfs.tar.xz": "051251cd87b3119d1d2ddcbfd65e474f8294dc503efadf0871f783d8b5f18999",
    "corpus/cases.json": "809861a02942a7c54dfcc322764b9c39ebee3eb8388756854855ea1d5b0f9e58",
    "protocol.json": "a934f7f00ba3212be94018a10b37f503449b94bbb37faafd332e7c0c5ed2989e",
}
GUARDED_SHA = {
    "dataset.json.gz": "67baf5d1b11cac52915e6f5af37ef180524b4800385e4907fd7978b8af0cf212",
    "receipts.tar.xz": "b10347ab699d2bb93dfaca333946baaeb0050287982c36f85d6169b6acf05545",
}


def identity(case):
    return {k: case[k] for k in JOIN_KEYS}


def match_case(expected, actual):
    require(identity(expected) == identity(actual), "case identifier, executable or input/output identity differs")


def read_json(path):
    data = path.read_bytes()
    return json.loads(gzip.decompress(data) if path.suffix == ".gz" else data)


def archived_reference(upstream):
    for name, digest in ARCHIVE_SHA.items():
        require(sha((upstream / name).read_bytes()) == digest, "different Aonohako #154 input: " + name)
    data = read_json(upstream / "measurements.json.gz")
    placement = read_json(upstream / "placement.json.gz")
    evaluation = read_json(upstream / "evaluation.json.gz")
    protocol = read_json(upstream / "protocol.json")
    require(data["protocol_sha256"] == evaluation["protocol_sha256"] == placement["protocol_sha256"]
            == ARCHIVE_SHA["protocol.json"], "inconsistent upstream protocol")
    by_case = {c["case_id"]: c for c in data["cases"]}
    upstream_cases = {c["case_id"]: c for c in read_json(upstream / "corpus/cases.json")}
    cases = {}
    with tarfile.open(upstream / "reports.tar.xz", "r:xz") as reports, \
            tarfile.open(upstream / "corpus/elfs.tar.xz", "r:xz") as binaries:
        for case in manifest():
            if case["population"] != "real":
                require(case["case_id"] not in by_case, "unexpected historical control")
                continue
            name = case["case_id"]
            raw = by_case[name]
            match_case(case, raw)
            original = upstream_cases[name]
            require(original["stdin"] == case["stdin"] and original["stdout"] == case["stdout"]
                    and original["input_sha256"] == sha(case["stdin"].encode()) == case["stdin_sha256"]
                    and original["expected_sha256"] == sha(case["stdout"].encode()) == case["stdout_sha256"]
                    and sha(binaries.extractfile("bin/" + case["program"]).read()) == case["binary_sha256"],
                    "upstream exact ELF or fixture differs: " + name)
            reduced = cases[name] = {**identity(case), "native": [], "metered": []}
            for kind in ("native", "metered"):
                for trial in sorted(raw[kind], key=lambda t: t["rep"]):
                    seq = trial["sequence"]
                    slot = {"case_id": name, "kind": kind, "rep": trial["rep"]}
                    placed = placement["trials"][seq]
                    actor = placed["roles"]["native" if kind == "native" else "metered_root"]
                    require(slot == protocol["schedule"][seq] and all(placed[k] == v for k, v in slot.items())
                            and all(e["cpu"] == 1 for e in actor["events"])
                            and trial["stdout_sha256"] == case["stdout_sha256"] and trial["exit_code"] == 0,
                            "unexpected historical launch")
                    r = {"rep": trial["rep"], "sequence": seq, "pid": actor["pid"]}
                    if kind == "native":
                        require(trial["cpu_us"] == trial["user_us"] + trial["sys_us"] > 0, "wrong native CPU")
                        r["cpu_ns"] = trial["cpu_us"] * 1000
                    else:
                        blob = reports.extractfile(trial["report_path"]).read()
                        report = json.loads(blob)
                        work = report["measurement"]
                        require(sha(blob) == trial["report_sha256"] and work["root_pid"] == r["pid"]
                                == report["execution"]["root_pid"] and work["quality"] == "uninterrupted"
                                and work["valid"] and work["complete"], "wrong historical process report")
                        counters = dict(instructions=work["instructions"], rep_bytes=work["rep_bytes"],
                                        movs_bytes=work["rep_breakdown"]["movs_bytes"],
                                        stos_bytes=work["rep_breakdown"]["stos_bytes"],
                                        cycles_x16=work["latency_bound"]["cycles_x16"],
                                        memory_operand_bytes=work["memory_operand_bytes"]["bytes"])
                        require(all(trial[k] == value for k, value in counters.items()), "wrong archived counters")
                        r.update(counters, report_sha256=sha(blob))
                    reduced[kind].append(r)
            require(len(reduced["native"]) == 7 and len(reduced["metered"]) == 2, "incomplete historical case")
    models = evaluation["models"]["cpp20_train_only"]
    return {"aonohako_commit": "b938afaa6c171a8a56e24d376d6662f93fcea4c6", "source_sha256": ARCHIVE_SHA,
            "reference_weights": models["instruction_movs_stos"]["weights_ns"],
            "combined_reference_weights": models["instruction_rep"]["weights_ns"], "cases": cases}


def observation(row, sample, production):
    pid = row["launcher_pid"]
    require(pid == sample["pid"] == production["root_pid"], "sample and clock came from different launches")
    hot_site(sample, "live_span", pid, {})
    sequence = row["sequence"]
    prefix = f"runs/{sequence:03d}/"
    return {"arm": row["arm"], "pid": pid, "sequence": sequence,
            "sample_sha256": row["receipts"][prefix + "sample.json"],
            "clock_sha256": row["receipts"][prefix + "clock.bin"],
            "production_sha256": row["receipts"][prefix + "production.json"],
            "instructions": production["instructions"], "rep_bytes": production["rep_bytes"],
            **{k: production["rep_breakdown"][k] for k in ("movs_bytes", "stos_bytes")},
            **{k: sample[k] for k in ("loads", "pairs", "near_pairs", "far_pairs")},
            "max_span_bytes": max((s["sampled_address_span_bytes"] for s in sample["sites"]), default=0)}


def pair(rep, kind, guarded, full, decision, actual=False):
    if decision is None:
        delivery = "first_full"
        obs = full
    elif decision["status"] == "use_profiled":
        delivery = "guarded"
        obs = guarded
    else:
        require(decision["status"] == "run_full" and full is not None, "missing required full sample")
        delivery = "actual_full_recovery" if actual else "matched_full_substitute"
        obs = full
    return {"rep": rep, "kind": kind, "guard_status": decision["status"] if decision else None,
            "guard_decision": decision, "delivery_source": delivery, "guarded": guarded, "full": full, "delivered": obs}


def evidence(upstream=None):
    require(sha(REFERENCE.read_bytes()) == REFERENCE_SHA, "changed compact historical reference")
    reference = read_json(REFERENCE)
    if upstream is not None:
        require(archived_reference(upstream) == reference, "historical reference differs from original evidence")
    for name, digest in GUARDED_SHA.items():
        require(sha((GUARDED / name).read_bytes()) == digest, "changed guarded source: " + name)
    guarded_verify.verify(GUARDED)
    data = read_json(GUARDED / "dataset.json.gz")
    cases = {}
    for c in manifest():
        item = cases[c["case_id"]] = {**identity(c), "population": c["population"], "rows": [],
                                      "archived_native_ns": [], "archived_metered": []}
        if c["population"] == "real":
            archive = reference["cases"][c["case_id"]]
            match_case(c, archive)
            item.update(archived_native_ns=[t["cpu_ns"] for t in archive["native"]], archived_metered=archive["metered"])
    samples = {}
    decisions = {}
    wanted = {"confirmation", "calibration", "guarded", "full", "audit_guarded", "audit_fallback"}
    with tarfile.open(GUARDED / "receipts.tar.xz", "r|xz") as archive:
        reports = {entry.name: json.load(archive.extractfile(entry)) for entry in archive
                   if entry.name.endswith(("/sample.json", "/production.json", "/guard.json"))}
    for r in data["rows"]:
        arm = r["arm"]
        if arm not in wanted:
            continue
        name, rep, seq = r["case_id"], r["rep"], r["sequence"]
        c = cases[name]
        require(r["elf_sha256"] == c["binary_sha256"] and r["stdout_sha256"] == c["stdout_sha256"],
                "guarded executable or output changed")
        prefix = f"runs/{seq:03d}/"
        sample = reports[prefix + "sample.json"]
        prod = reports[prefix + "production.json"]
        key = name, rep, arm
        samples[key] = observation(r, sample, prod)
        if arm in ("guarded", "audit_guarded"):
            audit = reports[prefix + "guard.json"]
            d = guard.decision(sample, audit, r["launcher_pid"])
            require(d == r["decision"], "incorrect recorded guard decision")
            decisions[key] = d
    for c in manifest():
        name = c["case_id"]
        rows = cases[name]["rows"]
        if c["split"] == "calibration":
            arm = "calibration" if c["program"] == "path_mix" else "confirmation"
            rows.append(pair(0, "first", None, samples[name, 0, arm], None))
            continue
        for rep in range(1, 9):
            key = name, rep, "guarded"
            rows.append(pair(rep, "scored", samples[key], samples[name, rep, "full"], decisions[key]))
        if name in data["recovery"]:
            key = name, 0, "audit_guarded"
            row = pair(0, "audit", samples[key], samples.get((name, 0, "audit_fallback")), decisions[key], actual=True)
            info = data["recovery"][name]
            require(row["delivered"]["sequence"] == info["selected_sequence"]
                    and row["delivered"]["sample_sha256"] == info["final_receipt_sha256"],
                    "audit selected a different run")
            rows.append(row)
    for case in cases.values():
        if case["archived_metered"]:
            # Independent launches do not have paired PIDs. Compare the complete observed ranges.
            historical = case["archived_metered"]
            delivered = [row["delivered"] for row in case["rows"]]
            case["delivered_minus_archived_range"] = {
                k: [min(c[k] for c in delivered) - max(c[k] for c in historical),
                    max(c[k] for c in delivered) - min(c[k] for c in historical)]
                for k in ("instructions", "movs_bytes", "stos_bytes")}
    return {"cases": cases, "reference_weights": reference["reference_weights"],
            "combined_reference_weights": reference["combined_reference_weights"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--aonohako", type=Path, help="Aonohako #154 docs/durante/gce-v11-cpp20-accuracy directory")
    args = parser.parse_args()
    out = evidence(args.aonohako)
    print(f"Verified {len(out['cases'])} exact inputs, {sum(len(c['rows']) for c in out['cases'].values())} observations.")
