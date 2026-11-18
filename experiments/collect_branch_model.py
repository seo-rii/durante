#!/usr/bin/env python3
"""Collect paired branch-model observations on the frozen nine-family CPP20 corpus."""
from functools import partial
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from collect_load_model import CORPUS, ROOT, collect, parse_args, prepare, quoted, run_with_meter
from cpp20_pattern_replay import REFERENCE, REFERENCE_SHA256
from reference_features import count, require, sha
from time_features import digest


BRANCH_CORPUS = ROOT / "experiments/branch_corpus.json"
GSHARE_PROFILE = "gshare-14bit-pcshift1-v1"


def load_corpus():
    extra = json.loads(BRANCH_CORPUS.read_bytes())
    base = CORPUS.read_bytes()
    require(extra["schema"] == 1 and extra["base_corpus_sha256"] == sha(base),
            "wrong base CPP20 corpus")
    corpus = json.loads(base)
    require(set(extra["families"]) == {"merge_streams", "hash_probe"} and
            not (extra["families"].keys() & corpus["families"].keys()), "unexpected prospective fixtures")
    corpus["families"].update(extra["families"])
    return corpus


def branch_counts(raw, production):
    require(raw["bpred"] is True and "load_pattern" not in raw, "expected gshare without load-pattern profiling")
    require(count(raw["instructions"]) and raw["instructions"] > 0 and
            raw["instructions"] == production["instructions"], "same-run instruction totals disagree")
    require(all(count(raw[k]) for k in ("loads", "cbr_executed", "cond_branches", "mispredicts")) and
            raw["cbr_executed"] == raw["cond_branches"] and
            raw["mispredicts"] <= raw["cbr_executed"] <= raw["instructions"] and
            raw["loads"] <= raw["instructions"], "invalid load or branch counts")
    return {"load_instructions": raw["loads"], "conditional_branches": raw["cbr_executed"],
            "gshare_mispredicts": raw["mispredicts"]}


def measure(tools, program, stdin, expected, folder, enabled, timeout, *, prefix):
    report = folder / "features.json"
    options = f"-out {quoted(report)} -bpred" if enabled else None
    _, cpu_ns, production = run_with_meter(tools, program, stdin, expected, folder, options,
                                            timeout, prefix=prefix)
    require(production["authority"] == "diagnostic_only" and production["integrity_verified"] is False and
            production["measurement_contract"]["counter_integrity"] == "application_mutable" and
            production["measurement_contract"]["trusted_verdict_eligible"] is False,
            "unexpected production counter contract")
    counters = {k: production[k] for k in ("instructions", "rep_bytes")}
    if enabled:
        counters.update(branch_counts(json.loads(report.read_bytes()), production))
    return cpu_ns, counters


def main():
    args = parse_args(__doc__)
    try:
        corpus = load_corpus()
        if args.action == "prepare":
            prepare(args, corpus)
            return
        reference = REFERENCE.read_bytes()
        require(sha(reference) == REFERENCE_SHA256, "wrong published judge reference")
        original = json.loads(reference)["tools_sha256"]
        expected = {k: original[k] for k in ("drrun", "runtime", "drmgr", "meter")}
        personality = subprocess.check_output(["setarch", "-R", "cat", "/proc/self/personality"],
                                              text=True, timeout=10).strip()
        require(int(personality, 16) & 0x40000, "ADDR_NO_RANDOMIZE unavailable")
        metadata = {"personality_hex": personality, "gshare_profile": GSHARE_PROFILE,
                    "base_corpus_sha256": digest(CORPUS), "reference_sha256": REFERENCE_SHA256,
                    "host_randomize_va_space": Path("/proc/sys/kernel/randomize_va_space").read_text().strip(),
                    "probe_options": ["-bpred"], "counter_integrity": "application_mutable"}
        # A private registrar directory makes the same exec prefix usable for
        # all four timed children, including on read-only worker home trees.
        with tempfile.TemporaryDirectory(dir=args.output.parent, prefix=".dr-registration-") as config:
            prefix = ("env", "DYNAMORIO_CONFIGDIR=" + config, "setarch", "-R")
            collect(args, corpus, corpus_path=BRANCH_CORPUS, native_prefix=prefix, metadata=metadata,
                    expected_tools=expected, measure=partial(measure, prefix=prefix))
    except (ValueError, TypeError, KeyError, OSError, subprocess.SubprocessError) as exc:
        sys.exit(f"CPP20 branch-model collection failed: {exc}")
    print(args.output / "collection.json")


if __name__ == "__main__":
    main()
