#!/usr/bin/env python3
"""Screen a gshare timing predictor on two prospectively held-out CPP20 workloads."""
import hashlib
import json
from pathlib import Path

import load_model


FAMILIES = {
    "training": ("integer", "rep_copy", "sort", "lower_bound", "window_sum"),
    "historical": ("branch", "rep_fill"),
    "unseen": ("merge_streams", "hash_probe"),
}
GSHARE_PROFILE = "gshare-14bit-pcshift1-v1"
MODELS = {"baseline": load_model.FEATURES,
          "with_gshare": (*load_model.FEATURES, "gshare_mispredicts")}


def evaluate(data):
    load_model.require(data["gshare_profile"] == GSHARE_PROFILE and data["probe_options"] == ["-bpred"]
                       and data["counter_integrity"] == "application_mutable", "unexpected gshare predictor profile")
    personality = data["personality_hex"]
    load_model.require(isinstance(personality, str) and len(personality) == 8 and
                       all(c in "0123456789abcdefABCDEF" for c in personality) and
                       int(personality, 16) & 0x40000, "expected fixed executable address layout")
    for case in data["cases"]:
        for row in case["repetitions"]:
            misses, branches = row["gshare_mispredicts"], row["conditional_branches"]
            load_model.require(load_model.integer(misses) and load_model.integer(branches)
                               and load_model.integer(row["instructions"], 1)
                               and misses <= branches <= row["instructions"],
                               "invalid conditional branch or gshare misprediction count")

    # The branch count is retained for validation and repeatability diagnostics.
    # Only the four predeclared predictors enter the candidate fit.
    result = load_model.evaluate(data, families=FAMILIES, models=MODELS,
                                 counters=(*MODELS["with_gshare"], "conditional_branches"))
    result["gshare_profile"] = GSHARE_PROFILE
    result["production_qualified"] = False
    return result


def check_manifest(data, corpus_bytes):
    from collect_branch_model import load_corpus
    from cpp20_pattern_replay import REFERENCE, REFERENCE_SHA256
    reference = REFERENCE.read_bytes()
    load_model.require(data["reference_sha256"] == REFERENCE_SHA256 == hashlib.sha256(reference).hexdigest(),
                       "unexpected judge reference collection")
    reference_tools = json.loads(reference)["tools_sha256"]
    load_model.require(all(data["tools_sha256"][name] == reference_tools[name]
                           for name in ("drrun", "runtime", "drmgr", "meter")),
                       "measurement tools differ from the judge reference")
    base_bytes = Path(__file__).with_name("load_corpus.json").read_bytes()
    required_base = json.loads(corpus_bytes)["base_corpus_sha256"]
    load_model.require(data["base_corpus_sha256"] == required_base == hashlib.sha256(base_bytes).hexdigest(),
                       "unexpected base corpus digest")
    load_model.check_corpus(data, corpus_bytes, expected=load_corpus())


if __name__ == "__main__":
    load_model.run_cli(evaluate, Path(__file__).with_name("branch_corpus.json"), __doc__,
                       check_manifest=check_manifest)
