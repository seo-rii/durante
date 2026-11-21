"""Shared registration, counterbalanced schedule and canonical receipt checks."""

import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
PLAN = HERE / "protocol.json"
BASE = HERE.parent / "search_order"
common = runpy.run_path(str(BASE / "study.py"))
prior, shared, production, require, sha = (
    common[k] for k in ("prior", "shared", "production", "require", "sha")
)
CASE_KEYS, MODES = common["CASE_KEYS"], common["MODES"]
input_manifest, read_inputs, schedule = (
    common[k] for k in ("input_manifest", "read_inputs", "schedule")
)
validate_sample = runpy.run_path(str(HERE / "probe/receipt.py"))["validate"]
CASES = (
    "fit_16k",
    "fit_256k",
    "fit_512k",
    "fit_1m",
    "fit_8m",
    "archived",
    "prior_nonpower",
    "new_96k",
    "new_384k",
    "new_768k",
    "new_2m",
    "new_4m",
)


def plan():
    value = json.loads(PLAN.read_bytes())
    cases = value["cases"]
    require(
        value["schema"] == 1
        and value["probe_schema"] == 2
        and value["rounds"] == 4
        and value["metered_phases"] == list(MODES)
        and tuple(c["workload_id"] for c in cases) == CASES,
        "unexpected footprint study design",
    )
    require(
        all(
            shared.digest(ROOT / path) == digest
            for path, digest in value["sources_sha256"].items()
        ),
        "registered source or fixture changed",
    )
    require(
        len(
            {
                (
                    c["binary_sha256"],
                    c["binary_archive"],
                    c["archive_member"],
                    c["terminal_offset"],
                )
                for c in cases
            }
        )
        == 1,
        "expected the same Rust ELF and terminal PC on every input",
    )
    for c in cases:
        raw = bytes.fromhex(c["stdin_hex"])
        require(
            c["input_file"] == c["workload_id"] + ".in"
            and len(raw) == c["input_size"]
            and sha(raw) == c["input_sha256"]
            and sha(bytes.fromhex(c["stdout_hex"])) == c["expected_sha256"],
            "incorrect registered literal input or output",
        )
    return value


def rows(data):
    value = plan()
    require(
        data["schema"] == 1
        and data["complete"] is True
        and data["cpu"] == 1
        and type(data["collector_pid"]) is int
        and data["collector_pid"] > 0
        and len(data["samples"]) == len(value["cases"]) * value["rounds"]
        and data["protocol_sha256"] == shared.digest(PLAN)
        and data["tools"] == value["tool_sha256"]
        and data["sources_sha256"] == value["sources_sha256"]
        and data["collector_sha256"] == shared.digest(HERE / "collect.py")
        and data["inputs_before"] == data["inputs_after"] == input_manifest(value),
        "incomplete study",
    )
    pids = {data["collector_pid"]}
    for (rep, case, phases), row in zip(schedule(value), data["samples"], strict=True):
        require(
            row["phase_order"] == phases
            and row["repetition"] == rep
            and all(row[k] == case[k] for k in CASE_KEYS),
            "mismatched workload or phase order",
        )
        for phase in phases:
            obs = row[phase]
            require(
                type(obs["pid"]) is int
                and obs["pid"] > 0
                and obs["pid"] not in pids
                and obs["exit_code"] == 0
                and obs["stdout_sha256"] == case["expected_sha256"]
                and obs["cpu_time_ns"] == obs["user_ns"] + obs["system_ns"] > 0
                and min(obs["user_ns"], obs["system_ns"]) >= 0
                and obs["wall_ns"] > 0,
                "invalid child receipt",
            )
            pids.add(obs["pid"])
            require(
                ("production" in obs) == (phase in MODES)
                and ("sample" in obs)
                == ("sample_sha256" in obs)
                == (phase == "sampled"),
                "invalid phase fields",
            )
            if phase in MODES:
                require(
                    obs["production"]["latency_bound"]["version"] == 9001,
                    "unexpected meter",
                )
            if phase == "sampled":
                sample = validate_sample(obs["sample"], obs["pid"])
                oracle = case["oracle"]
                require(
                    (
                        sample["pc"],
                        sample["visits"],
                        sample["pairs"],
                        sample["suffix_sum"],
                    )
                    == (
                        case["terminal_offset"],
                        oracle["queries"],
                        oracle["sample_pairs"],
                        oracle["sample_suffix_sum"],
                    ),
                    "sampled terminal loads differ from the executable/input oracle",
                )
                require(
                    sample["suffix_histogram"] == oracle["sampled_suffix_histogram"],
                    "sampled suffix widths differ from the independent input oracle",
                )
                require(
                    (sample["sampled_min_index"], sample["sampled_max_index"])
                    == (oracle["sample_min_index"], oracle["sample_max_index"]),
                    "sampled terminal-index extrema disagree with the input",
                )
        yield row, case, phases
