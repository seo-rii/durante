"""Replay guard decisions, conditional full runs, scheduler placement and release v11 records."""
import argparse
import json
from pathlib import Path
import sys
import tarfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import read_compressed, require, sha, verify_collection, verify_elfs
from first_run_sampler.prepare import control_binary
from guarded_sampler.cases import manifest
from guarded_sampler.export import GUARDED, SAMPLED
from guarded_sampler.guard import decision
from guarded_sampler.prepare import FIRST, mix_binary, profiles_from_results, study_files
from guarded_sampler.schedule import AUDITS, build, effective_schedule

RESULT = HERE.parent / "results/gce-guarded-sampler"


def verify_recovery(result, data):
    """Follow each observed guard decision to its actual terminal sample, including any rerun."""
    audits = {(r["case_id"], r["arm"]): r for r in data["rows"] if r["arm"].startswith("audit_")}
    with tarfile.open(result / "receipts.tar.xz", "r:xz") as archive:
        for case in AUDITS:
            guarded = audits[case, "audit_guarded"]
            prefix = f"runs/{guarded['sequence']:03d}/"
            sample = json.load(archive.extractfile(prefix + "sample.json"))
            guard = json.load(archive.extractfile(prefix + "guard.json"))
            observed = decision(sample, guard, guarded["launcher_pid"])
            recorded = data["recovery"][case]
            require(observed == recorded["decision"] and recorded["guarded_sequence"] == guarded["sequence"],
                    "audit decision differs from guard receipt: " + case)
            fallback = audits.get((case, "audit_fallback"))
            rerun = observed["status"] == "run_full"
            require(rerun == (fallback is not None), "wrong recovery branch: " + case)
            if fallback is not None:
                require(fallback["sequence"] == guarded["sequence"] + 1 and
                        guarded["elf_sha256"] == fallback["elf_sha256"], "wrong rerun provenance: " + case)
            final = fallback if rerun else guarded
            sequence = final["sequence"]
            name = f"runs/{sequence:03d}/sample.json"
            require(recorded["fallback_sequence"] == (sequence if rerun else None)
                    and recorded["selected_sequence"] == sequence
                    and recorded["final_source"] == ("full" if rerun else "guarded")
                    and recorded["final_receipt"] == name
                    and recorded["final_receipt_sha256"] == final["receipts"][name], "wrong final sample: " + case)


def verify(result, elf_archive=None):
    protocol = (HERE / "protocol.json").read_bytes()
    plan = json.loads(protocol)
    tools = plan["tools_sha256"]
    cases = manifest()
    require(cases == json.loads((HERE / "cases.json").read_bytes()) and plan["schedule"] == build(cases),
            "wrong cases or schedule")
    previous = json.loads((FIRST / "protocol.json").read_bytes())
    require(all(previous[k] == plan[k] for k in ("cpu", "work_budget", "poll_interval_ms", "trial_timeout_s")),
            "changed clock or placement configuration")
    pinned = {k for k in previous["tools_sha256"] if k.startswith(("bin/", "dynamorio/"))}
    pinned.update(("v11.so", "profiled.so", "span.so", "production_reader.py", "profile.py"))
    require(all(tools[k] == previous["tools_sha256"][k] for k in pinned), "changed runtime, ELF, client or reader")
    for name, source in study_files().items():
        require(tools[name] == sha(source.read_bytes()), "changed collector: " + name)
    for digest, profile in profiles_from_results().items():
        require(tools["profiles/" + digest + ".json"] == sha(profile), "changed source profile")
    require(sha(mix_binary()) == tools["bin/path_mix"] and sha(control_binary()) == tools["bin/path_switch"],
            "changed control executable")
    if elf_archive:
        verify_elfs(elf_archive, [c for c in cases if c["population"] == "real"])
    for c in cases:
        require(tools["bin/" + c["program"]] == c["binary_sha256"] and
                all(sha(c[k].encode()) == c[k + "_sha256"] for k in ("stdin", "stdout")), "wrong case identity")

    data = read_compressed(result / "dataset.json.gz")
    schedule = effective_schedule(plan, data["recovery"])
    extra = {arm: {"guard.json"} for arm in GUARDED} | {"calibration": {"profile.json"}}
    counts = verify_collection(result, protocol, cases, SAMPLED, SAMPLED | {"live"},
                               extra_receipts_by_arm=extra, schedule=schedule)
    verify_recovery(result, data)
    return counts


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("result", type=Path, default=RESULT, nargs="?")
    cli.add_argument("--elf-archive", type=Path, help="Aonohako #154 corpus/elfs.tar.xz")
    args = cli.parse_args()
    trials, records, clocks = verify(args.result, args.elf_archive)
    print(f"Verified {trials} CPU-pinned runs, {records} process reports, {clocks} live clocks and three recovery audits.")
