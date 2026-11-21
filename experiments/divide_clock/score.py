"""Fit divider parameters on the first assembly controls and replay a frozen selection."""
import argparse
from fractions import Fraction
from functools import lru_cache
import json
from pathlib import Path
from statistics import median

from corpus import HERE, ROOT, require, sha
from replay import block_x16

PLAN = HERE / "protocol.json"
PARAMETERS = HERE / "parameters.json"
HISTORICAL = ROOT / "experiments/results/divide-clock"
FAMILIES = ("v1", "zero", "latency", "latency_interval")


def coefficients(plan):
    raw = (ROOT / plan["reference"]).read_bytes()
    require(sha(raw) == plan["reference_sha256"], "historical coefficient source changed")
    saved = json.loads(raw)["models_on_medians"]["latency_memory"]["coefficients_ns"]
    return Fraction(saved["latency_cycles"]), Fraction(saved["memory_bytes"])


@lru_cache(maxsize=32768)
def changed_bound(shape, latency, interval, zero):
    block = json.loads(shape)
    return block_x16(block, latency, interval, zero) - block["v1_x16"]


def prepare(case):
    runs = []
    for obs in case["runs"]:
        counters = obs["production"]
        blocks = [(b["entries"], json.dumps({k: val for k, val in b.items() if k != "entries"},
                                             separators=(",", ":"), sort_keys=True)) for b in obs["feature"]["blocks"]]
        runs.append((counters["latency_bound"]["cycles_x16"], counters["memory_operand_bytes"]["bytes"], blocks))
    return {"target": median(map(Fraction, case["native_ns"])), "native": case["native_ns"], "runs": runs}


def projected_x16(run, params):
    base, _, blocks = run
    latency, interval, zero = params
    cycles = base + sum(n * changed_bound(shape, latency, interval, zero) for n, shape in blocks)
    require(cycles > 0, "nonpositive adjusted block-cycle total")
    return cycles


def predictions(case, params, weights):
    a, b = weights
    return [a * projected_x16(run, params) / 16 + b * run[1] for run in case["runs"]]


def fit(cases, plan, weights):
    training = [cases[name] for name in plan["train"]]
    limits = plan["latency_cycles"], plan["interval_cycles"]
    def loss(params):
        return sum((median(predictions(c, params, weights)) / c["target"] - 1)**2 for c in training)
    def pick(allow_interval):
        candidates = ((latency, interval, True) for latency in range(limits[0][0], limits[0][1] + 1)
                      for interval in range(limits[1][0], min(limits[1][1], latency) + 1 if allow_interval else 1))
        return min(candidates, key=lambda v: (loss(v), v[1], abs(v[0]-20), v[0]))
    selected = {"v1": (20, 0, False), "zero": (20, 0, True),
                "latency": pick(False), "latency_interval": pick(True)}
    return {name: list(v) for name, v in selected.items()}


def summarize(cases, groups, selection, plan, weights):
    tolerance = Fraction(plan["tolerance_ppm"], 1_000_000)
    rows = {}
    for name, c in cases.items():
        native = c["native"]
        target = c["target"]
        preds = {}
        for model, params in selection.items():
            estimates = predictions(c, params, weights)
            errors = [t/target-1 for t in estimates]
            preds[model] = {"estimates_ns": [round(n) for n in estimates],
                            "cycles_x16": [projected_x16(run,params) for run in c["runs"]],
                            "signed_errors_ppm": [round(e*1_000_000) for e in errors],
                            "pass": max(map(abs,errors)) <= tolerance}
        rows[name] = {"native_min_ns": min(native), "native_median_ns": str(target), "native_max_ns": max(native),
                      "duration_pass": min(native) >= plan["native_floor_ns"],
                      "spread_pass": (max(native)-min(native))*1_000_000 <= plan["max_spread_ppm"]*target,
                      "models": preds}
    totals = {group: {model: sum(rows[name]["models"][model]["pass"] for name in members)
                      for model in FAMILIES} for group,members in groups.items()}
    return {"cases": rows, "passed": totals}


def historical():
    import importlib.util
    spec = importlib.util.spec_from_file_location("divide_collection", HERE / "collect.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    raw = mod.read_observations(HISTORICAL/"observations.json.gz", HISTORICAL/"receipts.tar.xz")
    return {name: prepare(v) for name,v in raw.items()}


def freeze(cases, plan, weights):
    return {"schema": 1, "protocol_sha256": sha(PLAN.read_bytes()),
            "observations_sha256": sha((HISTORICAL/"observations.json.gz").read_bytes()),
            "receipts_sha256": sha((HISTORICAL/"receipts.tar.xz").read_bytes()),
            "training": plan["train"], "parameter_order": ["latency_cycles", "interval_cycles", "break_zero_input"],
            "models": fit(cases,plan,weights)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--freeze", action="store_true", help="create the historical selection before fresh acquisition")
    parser.add_argument("--verify", action="store_true", help="verify archived evaluation")
    args = parser.parse_args()
    plan = json.loads(PLAN.read_bytes())
    weights = coefficients(plan)
    cases = historical()
    selection = freeze(cases,plan,weights)
    if args.freeze:
        with PARAMETERS.open("x") as out:
            out.write(json.dumps(selection,indent=2)+"\n")
    require(json.loads(PARAMETERS.read_bytes()) == selection, "frozen divider parameters disagree with calibration")
    groups = {g: plan[g] for g in ("train","assembly_validation","rust_validation")}
    result = {"parameters_sha256": sha(PARAMETERS.read_bytes()),
              **summarize(cases, groups, selection["models"],plan,weights)}
    output = HISTORICAL/"evaluation.json"
    if args.verify:
        require(json.loads(output.read_bytes()) == result, "historical evaluation differs")
    else:
        output.write_text(json.dumps(result,indent=2)+"\n")
    print(json.dumps({"selected":selection["models"],"passed":result["passed"]}))


if __name__ == "__main__":
    main()
