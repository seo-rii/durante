"""Score prespecified v12 counter models on completed CPU-pinned CPP20 runs.

Every fit uses exactly the three original references. Native times on the
twelve held-out input/submission pairs are used only for scoring. Brackets
compare hypothetical deadline classifications from completed counters.
"""
import argparse
import base64
from collections import Counter
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import re
from statistics import mean, median
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from rust_instruction_mix.score import center, digest, fit, require, uint
from guarded_native.anchor import static_elf

PROBLEMS = ("range_min", "shortest_paths", "inversions")
FEATURES = ("instructions", "work", "latency_x16", "memory_operand_bytes")
# Aonohako's pinned-judge pilot, fit to the three earlier references and
# frozen before either held-out judging guest.
FROZEN = Fraction(16_156_000, 63_315_373)
RELATIVE_MODELS = {
    "latency_memory": ("latency_x16", "memory_operand_bytes"),
    "instructions_memory": ("instructions", "memory_operand_bytes"),
    "latency_only": ("latency_x16",),
}
HARD = {"cpu_time_ms": 120000, "wall_time_ms": 170000, "memory_mb": 1024}
NOMINAL = {"time_ms": 30000, "wall_time_ms": 60000, "memory_mb": 768, "output_bytes": 8 << 20}
NUMBER = {"native": 5, "counters": 2}
EXEC_EVENT = re.compile(r"\[(\d+)\].*?sched_process_exec: filename=(.*?) pid=(\d+) old_pid=")
DRRUN = "/opt/durante/dynamorio/bin64/drrun"
MANIFEST_SHA = "30e57d6ee28ef1b857e25948fa280abf3f914476b29cd7682221944de32e9e4c"
PILOT_REGISTRATION_SHA = "4b74d1abfcde3923bc3d9ded82beb3967936a0ccc58636b3cde03d74c5316f06"
# From the pilot registration and its immutable runtime plan.
PILOT_SERVER_SHA = "990c60741226efe1e2a1b57bef16f4fc56c3f811b877f5565fb6244efac70e57"
INSTALLATION = {
    "launcher_sha256": "0d059cb874edc7cfea3036c65dc975c1b0504464182980036a062df7d9bc206b",
    "client_sha256": "27051cef2450f484c08d79023607e39a6e75a8d7c22b077e028379e3b51d921c",
    "release_core_sha256": "f2478395d33e2d8e4a6fb4158fd826777cca37ee02e94924757696b08d7fb06f",
}


def fit_models(training):
    require(len(training) == 3 and all(len(s["native"]) == 5 and len(s["counters"]) == 2 for s in training),
            "expected exactly three calibration references")
    observations = [({k: center([r[k] for r in case["counters"]]) for k in FEATURES}, median(case["native"]))
                    for case in training]
    slope = sum(row["work"] * ns for row, ns in observations) / sum(row["work"]**2 for row, _ in observations)
    models = {"frozen_shared": {"work": FROZEN}, "absolute_work": {"work": slope}}
    for name, columns in RELATIVE_MODELS.items():
        models[name], _ = fit(observations, columns)
    return models


def percent(value):
    return round(float(value * 100), 4)


def ratio(value):
    return {"numerator": value.numerator, "denominator": value.denominator}


def evaluate(weights, cases):
    both_within = 0
    rep_errors, center_errors, details = [], [], {}
    confusion = {"low": Counter(), "high": Counter()}
    for case_id, samples in cases.items():
        native = samples["native"]
        native_ns = median(native)
        estimates = [sum(w * counters[k] for k, w in weights.items()) for counters in samples["counters"]]
        errors = [ns / native_ns - 1 for ns in estimates]
        both_within += all(abs(e) <= Fraction(1, 5) for e in errors)
        rep_errors.extend(errors)
        center_errors.append(median(errors))
        brackets = {}
        for name, fraction in (("low", Fraction(4, 5)), ("high", Fraction(6, 5))):
            limit = fraction * native_ns
            observed = sum(n > limit for n in native)
            expected = observed > len(native) // 2
            predicted = [n > limit for n in estimates]
            for stop in predicted:
                label = ("true_" if stop == expected else "false_") + ("tle" if stop else "pass")
                confusion[name][label] += 1
            brackets[name] = {"native_over_limit": observed, "predictions_over_limit": sum(predicted)}
        details[case_id] = dict(native_median_ns=native_ns, estimated_ns=[round(x) for x in estimates],
                                signed_error_pct=[percent(e) for e in errors], hypothetical_brackets=brackets)
    all_confusion = {bracket: {k: counter[k] for k in ("true_pass", "false_pass", "true_tle", "false_tle")}
                     for bracket, counter in confusion.items()}
    return dict(cases=len(cases), cases_both_within_20pct=both_within,
                counter_runs_within_20pct=sum(abs(e) <= Fraction(1,5) for e in rep_errors), counter_runs=len(rep_errors),
                median_signed_error_pct=percent(median(center_errors)), median_absolute_error_pct=percent(median(map(abs, center_errors))),
                mean_absolute_error_pct=percent(mean(map(abs, center_errors))), worst_abs_error_pct=percent(max(map(abs, rep_errors))),
                hypothetical_confusion=all_confusion, pairs=details)


def score(samples, cases):
    training = [samples[c["case_id"]] for c in cases if c["split"] == "calibration"]
    held_out = {c["case_id"]: samples[c["case_id"]] for c in cases if c["split"] == "held_out"}
    require(len(training) == 3 and len(held_out) == 12, "wrong scoring split")
    models = fit_models(training)
    return {name: {"nanoseconds_per_unit": {k: ratio(v) for k,v in model.items()}, **evaluate(model, held_out)}
            for name, model in models.items()}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read(root, name):
    path = root / name
    if path.is_file():
        require(not (root / (name + ".gz")).exists(), f"ambiguous evidence file {name}")
        return path.read_bytes()
    return gzip.decompress((root / (name + ".gz")).read_bytes())


def check_cases(cases):
    required = {(p, "original", "reference") for p in PROBLEMS}
    required |= {(p, variant, role) for p in PROBLEMS for variant in ("a", "b") for role in ("fast", "wrong")}
    seen, inputs, programs = set(), {}, {}
    for c in cases:
        p, variant, role = c["problem"], c["variant"], c["role"]
        require((p, variant, role) in required and c["case_id"] == f"{p}-{variant}-{role}" and
                c["split"] == ("calibration" if variant == "original" else "held_out"), "wrong registered split or case ID")
        required.remove((p, variant, role))
        for literal, hash_key in (("stdin","stdin_sha256"), ("expected_stdout","stdout_sha256"), ("solver_stdout","solver_stdout_sha256")):
            require(sha(c[literal].encode("ascii")) == c[hash_key], f"incorrect registered {literal} digest")
        require(c["expected_status"] == ("Wrong Answer" if role == "wrong" else "Accepted") and
                (c["solver_stdout"] == c["expected_stdout"]) == (role != "wrong"), "wrong registered output oracle")
        binary, stdin = c["binary_sha256"], c["stdin_sha256"]
        require(digest(binary) and c["binary"] == f"{p}-{role}" and (binary,stdin) not in seen, "duplicate or invalid ELF/input pair")
        seen.add((binary,stdin))
        require(programs.setdefault((p,role), binary) == binary and
                inputs.setdefault((p,variant), (stdin,c["stdout_sha256"])) == (stdin,c["stdout_sha256"]), "inconsistent pair identities")
    require(not required and len(set(programs.values())) == 9 and len({v[0] for v in inputs.values()}) == 9,
            "missing, reused or unregistered workloads")


def schedule(cases):
    for rep in range(1, 6):
        for offset in range(len(cases)):
            case = cases[(offset + 3*(rep-1)) % len(cases)]
            kinds = ("native", "counters") if (offset + rep) % 2 else ("counters","native")
            for kind in kinds:
                if rep <= NUMBER[kind]:
                    yield case, kind, rep


def read_trace(raw):
    windows = []
    for line in raw.decode().splitlines():
        if line.startswith("# trial "):
            require(line == f"# trial {len(windows)}", "kernel trace sequence differs")
            windows.append([])
        elif line.strip():
            match = EXEC_EVENT.search(line)
            require(windows and match, "invalid sched_process_exec event")
            cpu, path, pid = match.groups()
            windows[-1].append({"cpu":int(cpu), "path":path, "pid":int(pid)})
    return windows


def placement(window, row):
    require(window == row["execs"] and all(e["cpu"] == 1 and e["pid"] > 0 for e in window), "incorrect CPU or kernel evidence")
    if row["kind"] == "native":
        target = [e for e in window if Path(e["path"]).name == "Main"]
        require(not any(e["path"] == DRRUN for e in window), "native target ran under measurement")
    else:
        target = [e for e in window if e["path"] == DRRUN]
    require(len(target) == 1, "missing or duplicate CPU 1 target")


def verify_native(receipt, case):
    ns = receipt["cpu_time_ns"]
    require(not any(k.startswith("work_measurement") for k in receipt) and receipt["status"] == case["expected_status"] and
            receipt["verdict_source"] == "stdout" and receipt["cpu_time_ms"] == -(-ns // 1_000_000),
            "invalid ordinary stdout verdict or native time")
    cpu = receipt["cpu_accounting"]
    waited, process = cpu["wait_target_ns"], cpu["process_clock_peak_ns"]
    require(waited == max(0, cpu["wait_user_ns"] + cpu["wait_system_ns"] - cpu["rusage_baseline_ns"]),
            "inconsistent wait CPU accounting")
    source = cpu["selected_source"]
    if source == "cgroup":
        require(ns >= max(waited, process), "cgroup CPU below target CPU")
    else:
        require(source in ("wait_rusage", "process_clock") and ns == max(waited, process) ==
                (waited if source == "wait_rusage" else process), "native CPU source or total differs")
    return ns


def verify_counters(receipt, case):
    measured = receipt["work_measurement"]
    env = receipt["work_measurement_execution"]
    require(receipt["status"] == "Container Initialization Failed" and receipt.get("score") is None and
            "work_measurement_judgment" not in receipt and "clock" not in env and "time_estimate" not in env and
            env["state"] == "complete" and env["output_matches"] is (case["expected_status"] == "Accepted") and
            env["termination_source"] == "stdout" and env["nominal_limits"] == NOMINAL and env["raw_limits"] == HARD and
            env["authority"] == measured["authority"] == "diagnostic_only", "invalid terminal diagnostic envelope")
    require(measured["complete"] is True and measured["integrity_verified"] is False and measured["family"] == "cpp" and
            measured["engine_profile"] == "dynamorio-11.3-durante-v12-vm32m" and measured["dispatch_profile"] == "default"
            and measured["cpuid_profile"] == "x86-64-v2" and measured["quality"] == "uninterrupted"
            and measured["processes"] == measured["images"] == 1 and measured["signals_observed"] == 0 and
            measured["unsupported_rep_entries"] == 0 and measured["rep_telemetry_status"] == "movs_stos_only", "ineligible counts")
    identity = measured["installation"]
    require(identity["unchanged_before_after"] is True and all(identity[k] == d for k,d in INSTALLATION.items()),
            "wrong v12 installation")
    rep, latency, memory = measured["rep_breakdown"], measured["latency_bound"], measured["memory_operand_bytes"]
    instructions = measured["instructions"]
    require(uint(instructions, 1) and uint(rep["movs_bytes"]) and uint(rep["stos_bytes"]) and
            uint(measured["rep_bytes"]) and measured["rep_bytes"] == rep["movs_bytes"] + rep["stos_bytes"] and
            rep["version"] == latency["version"] == memory["version"] == 1 and uint(latency["cycles_x16"], 4*instructions)
            and uint(memory["bytes"], 1), "missing or inconsistent REP, latency or memory counters")
    return dict(instructions=instructions, work=instructions + measured["rep_bytes"], latency_x16=latency["cycles_x16"],
                memory_operand_bytes=memory["bytes"])


def request_hash(payload, case, kind):
    executable = payload / "pilot" / "corpus" / case["binary"]
    require(static_elf(executable) == case["binary_sha256"], "payload executable differs from registered static ELF")
    req = dict(problem_id=case["problem"], lang="CPP20", binaries=[dict(name="Main", mode="exec",
               data_b64=base64.b64encode(executable.read_bytes()).decode())],
               stdin=case["stdin"], expected_stdout=case["expected_stdout"], limits=NOMINAL, emit_logs=True)
    if kind == "counters":
        req.update(work_measurement=True, work_measurement_limits=HARD)
    return sha(json.dumps(req).encode())


def load_trials(rows, windows, cases, payload):
    hashes, samples = {}, {case["case_id"]: {"native": [], "counters": []} for case in cases}
    for sequence, ((case, kind, rep), row, window) in enumerate(zip(schedule(cases), rows, windows, strict=True)):
        require(row["sequence"] == sequence and row["kind"] == kind and type(row["rep"]) is int and row["rep"] == rep and
                all(row[k] == case[k] for k in ("case_id", "split", "problem", "role", "binary_sha256", "stdin_sha256")),
                f"trial order or identity differs at {sequence}")
        require(row["expected_stdout_sha256"] == case["stdout_sha256"] and row["stdout"] == case["solver_stdout"] and
                row["stdout_sha256"] == sha(row["stdout"].encode()) == case["solver_stdout_sha256"], "solver or expected output differs")
        key = case["case_id"], kind
        digest_on_wire = row["request_sha256"]
        if key not in hashes:
            require(digest(digest_on_wire) and digest_on_wire not in hashes.values(), "missing or duplicate request digest")
            hashes[key] = request_hash(payload, case, kind) if payload else digest_on_wire
        require(hashes[key] == digest_on_wire, "registered request bytes differ")
        placement(window, row)
        receipt = row["receipt"]
        require(receipt.get("exit_code") == 0 and uint(receipt["cpu_time_ns"], 1) and
                not any(receipt.get(k) for k in ("cpu_time_normalization", "cpu_time_sampling", "stdout_truncated", "stderr_truncated")),
                "nonterminal, truncated, normalized or sampled run")
        sample = verify_native(receipt, case) if kind == "native" else verify_counters(receipt, case)
        samples[case["case_id"]][kind].append(sample)
    return samples


def check_infrastructure(meta, host, registration, journal, trace):
    require((meta["phase"], meta["zone"], meta["machine_type"], meta["cpu_platform"], meta["threads_per_core"], meta["image"]) ==
            ("features", "asia-northeast3-a", "n2d-standard-4", "AMD Milan", 1, "debian-12-bookworm-v20261117")
            and meta["disk_auto_delete"] is True and meta["external_ip"] is False and meta["service_account"] is False and
            meta["cleanup_http"] == {"instance":404, "boot_disk":404}, "different GCE setup or incomplete cleanup")
    require(meta["boot_sha256"] == host["boot_sha256"] and meta["trials_sha256"] == sha(journal) and
            meta["exec_trace_sha256"] == sha(trace) and meta["archive_sha256"] == registration["archive_sha256"] and
            all(digest(meta[k]) for k in ("instance_id_sha256", "disk_id_sha256")), "GCE evidence digest mismatch")


def analyze(root, evidence, payload=None, infrastructure=None):
    manifest_raw = (root / "case_manifest.json").read_bytes()
    manifest = json.loads(manifest_raw)
    registration = json.loads((root / "registration.json").read_bytes())
    files = registration["payload"]
    require(sha(manifest_raw) == files["case_manifest.json"] == MANIFEST_SHA and
            manifest["registration_sha256"] == registration["pilot_registration_sha256"] == PILOT_REGISTRATION_SHA and
            manifest["aonohako_commit"] == registration["pilot_commit"], "case manifest or source registration changed")
    require(all(files[name] == sha((root / name).read_bytes()) for name in ("guest.py", "guest.sh")), "different collection driver")
    cases = manifest["cases"]
    check_cases(cases)
    for case in cases:
        require(case["binary_sha256"] == files["pilot/corpus/" + case["binary"]], "changed registered submission")
    host = json.loads(read(evidence, "platform.json"))
    require(host["cpu"] == 1 and host["affinity"] == [1] and host["cpuset_cpus_effective"] == host["cpu1_thread_siblings"] == "1"
            and set(host["workers"]) == {"native", "counters"} and len(set(host["workers"].values())) == 2 and
            all(uint(pid, 1) for pid in host["workers"].values()) and digest(host["boot_sha256"]) and
            0 <= host["steal_ticks_before"] <= host["steal_ticks_after"], "invalid CPU 1 placement or worker identities")
    require(host["case_manifest_sha256"] == MANIFEST_SHA and host["plan_sha256"] == files["pilot/plan.json"] and
            host["runtime_sha256"] == files["pilot/runtime.squashfs"] and host["server_sha256"] == PILOT_SERVER_SHA,
            "guest runtime or manifest differs from registered payload")
    journal, trace = read(evidence, "trials.jsonl"), read(evidence, "exec-trace.txt")
    samples = load_trials(map(json.loads, journal.splitlines()), read_trace(trace), cases, payload)
    if infrastructure:
        check_infrastructure(json.loads(infrastructure.read_bytes()), host, registration, journal, trace)
    return dict(schema=1, method="Terminal-counter CPU-time estimates and hypothetical completed-run deadline classifications.",
                training_cases=3, held_out_cases=12, native_trials=75, diagnostic_trials=30,
                bracket_rule="Strictly over 80% or 120% of that case's five-run native median, compared with the native majority.",
                model_error="Signed error uses the median native CPU time as denominator. Summary medians weight each held-out pair equally.",
                registration_sha256=sha((root / "registration.json").read_bytes()), case_manifest_sha256=MANIFEST_SHA,
                trials_sha256=sha(journal), exec_trace_sha256=sha(trace), models=score(samples, cases))


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--root", type=Path, default=HERE)
    cli.add_argument("--evidence", type=Path, required=True)
    cli.add_argument("--infrastructure", type=Path)
    cli.add_argument("--payload", type=Path, help="reconstruct the exact HTTP request with its original static executable")
    cli.add_argument("--output", type=Path)
    args = cli.parse_args()
    text = json.dumps(analyze(args.root, args.evidence, args.payload, args.infrastructure), indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")
