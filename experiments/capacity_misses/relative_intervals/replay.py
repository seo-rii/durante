"""Estimate the transfer of an empirical interval around relative clock scores."""
import argparse
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
import json
from pathlib import Path
import runpy

HERE = Path(__file__).resolve().parent
CAPACITY = HERE.parent
PHASES = ("primary", "repeat")


def read(path):
    return json.loads(path.read_bytes())


@dataclass(frozen=True)
class Interval:
    low: Fraction
    high: Fraction

    def __post_init__(self):
        if not 0 < self.low <= self.high:
            raise ValueError("a time ratio needs ordered, positive bounds")

    @classmethod
    def from_runs(cls, solver, reference):
        if not solver or not reference or min(solver) <= 0 or min(reference) <= 0:
            raise ValueError("time ratio requires positive observations from both programs")
        return cls(Fraction(min(solver), max(reference)), Fraction(max(solver), min(reference)))

    def verdict(self, deadline):
        if self.high <= deadline:
            return "accept"
        if self.low > deadline:
            return "timeout"
        return "mixed"

    def times(self, other):
        return Interval(self.low * other.low, self.high * other.high)

    def exact(self):
        return [str(self.low), str(self.high)]


@dataclass(frozen=True)
class Observation:
    family: str
    boot: str
    case: str
    native: Interval
    model: Interval


def calibration(rows):
    if not rows:
        raise ValueError("no calibration observations")
    return Interval(min(r.native.low / r.model.high for r in rows),
                    max(r.native.high / r.model.low for r in rows))


def evaluate(rows, correction, deadlines):
    counts = Counter(cells=0, native_mixed=0, issued_native_mixed=0,
                     correct=0, false_accept=0, false_timeout=0, abstain=0)
    issued_verdicts = Counter(accept=0, timeout=0)
    details = {}
    for row in rows:
        if row.case in details:
            raise ValueError("duplicate case within the same boot")
        prediction = row.model.times(correction)
        outcomes = []
        for limit in deadlines:
            truth, call = row.native.verdict(limit), prediction.verdict(limit)
            if call != "mixed":
                issued_verdicts[call] += 1
            counts["cells"] += 1
            counts["native_mixed"] += truth == "mixed"
            if call == "mixed":
                outcome = "abstain"
            elif truth == "mixed":
                outcome = "issued_native_mixed"
            elif call == truth:
                outcome = "correct"
            else:
                outcome = "false_" + call
            counts[outcome] += 1
            outcomes.append(outcome)
        details[row.case] = dict(native=row.native.exact(), model=row.model.exact(),
                                 adjusted=prediction.exact(), outcomes=outcomes)
    issued = counts["cells"] - counts["abstain"] - counts["issued_native_mixed"]
    decisive = counts["cells"] - counts["native_mixed"]
    return dict(counts=counts, issued_verdicts=issued_verdicts, issued_on_decisive=[issued, decisive], cases=details)


def record(family, phase, names, samples):
    anchor, *alternatives = names
    anchor_native, anchor_score = samples[anchor]
    for name in alternatives:
        native, score = samples[name]
        yield Observation(family, phase, name, Interval.from_runs(native, anchor_native),
                          Interval.from_runs(score, anchor_score))


def modern(directory, plan, reg, freeze, group_families, tools, penalty, weights):
    """Use the registered cache and branch readers, keeping each guest separate."""
    previous = tools["previous"]
    evidence = directory / "evidence"
    cloud = read(evidence / "cloud.json")
    clients = {arm: plan["clients"][name] for arm, name in plan["protocol"]["clients"].items()}
    require = previous["require"]
    require(plan["ml4"] == weights, "different ML4 coefficients")
    for key in ("instance_id", "boot_disk_id", "boot_sha256"):
        require(cloud["primary"][key] != cloud["repeat"][key], "same guest used twice")
    ns_weights = {key: Fraction(str(value)) * 10**9 for key, value in weights.items()}
    for phase in PHASES:
        arms, _ = previous["read_boot"](phase, cloud[phase], reg, plan, evidence=evidence, freeze=freeze,
            client_by_arm=clients, diagnostic_by_arm={"flat": "outer", "outer": "outer", "branch": "latency"})
        previous["read_branch"](evidence / (phase + ".tar.gz"), arms)
        samples = {}
        for name, measurements in arms.items():
            estimates, _ = tools["measured"](measurements, ns_weights, penalty)
            samples[name] = ([row["cpu_us"] for row in measurements["native"]], estimates["ML4_plus_misses"])
        for group, names in plan["protocol"]["groups"].items():
            yield from record(group_families[group], phase, names, samples)


def old_dijkstra(tools, weights, penalty):
    directory = CAPACITY / "milan"
    plan = read(directory / "frozen.json")
    reg = read(directory / "registration.json")
    reg["payload"] = {f["path"]: f["sha256"] for f in reg["files"]}
    gshare = tools["previous"]["gshare"]
    milan = tools["milan"]
    require = tools["require"]
    require(plan["weights"]["dijkstra"]["ML4"] == weights, "different Dijkstra weights")
    evidence = read(directory / "gshare/records.json")
    gshare["evaluate"](evidence)  # verify the local branch observations and ELF/input identities
    names = plan["protocol"]["groups"]["dijkstra"]
    misses = {name: [] for name in names}
    for r in evidence["rows"]:
        name = r["case"]
        if name in misses:
            misses[name].append(gshare["restored"](r, plan["cases"][name])["gshare_mispredicts"])
    ns_weights = {key: Fraction(str(v)) * 10**9 for key, v in weights.items()}
    for phase, host in read(directory / "evidence/cloud.json").items():
        data, _ = milan.read_boot(directory / "evidence" / (phase + ".tar.gz"), phase, plan, reg, host)
        samples = {}
        for name in names:
            base = [milan.predict(ns_weights, milan.features(p)) for p in data[name]["probe"]]
            samples[name] = (data[name]["native"], [b + penalty * m for b in base for m in misses[name]])
        yield from record("historical_dijkstra", phase, names, samples)


def report(training, targets, deadlines, prospective=False):
    correction = calibration(training)
    phases = {}
    for phase in PHASES:
        cases = [c for c in targets if c.boot == phase]
        phases[phase] = dict(point=evaluate(cases, Interval(Fraction(1), Fraction(1)), deadlines),
                             calibrated=evaluate(cases, correction, deadlines))
    result = dict(training_families=sorted({r.family for r in training}), multiplier=correction.exact(), boots=phases)
    if prospective:
        passed = {}
        for phase, data in phases.items():
            counts = data["calibrated"]["counts"]
            passed[phase] = (counts["correct"] > 0 and counts["false_accept"] == counts["false_timeout"] == 0
                             and counts["issued_native_mixed"] == 0)
        result["registered_criterion"] = dict(by_boot=passed, both_boots=all(passed.values()))
    return result


def replay(prospective=None):
    protocol = read(HERE / "protocol.json")
    deadlines = [Fraction(value) for value in protocol["thresholds"]]
    tools = runpy.run_path(str(CAPACITY / "range_distinct/replay.py"))
    previous = tools["previous"]
    reg, plan, branch = previous["registered"]()
    weights = plan["ml4"]
    penalty = Fraction(protocol["branch_miss_ns"])
    tools["require"](penalty == branch["gshare_mispredicts"], "different gshare coefficient")
    families = {g: "predecessor" for g in plan["protocol"]["training_examples"]}
    families.update({g: "inversions" for g in plan["protocol"]["holdout"]})
    rows = list(modern(previous["HERE"], plan, reg, previous["FREEZE"], families, tools, penalty, weights))
    plan, reg = tools["registered"]()
    rows.extend(modern(tools["HERE"], plan, reg, tools["FREEZE"],
                       dict.fromkeys(plan["protocol"]["groups"], "range_distinct"), tools, penalty, weights))
    history = list(old_dijkstra(tools, weights, penalty))
    out = {}
    for family in protocol["calibration_families"]:
        out[family] = report([r for r in rows if r.family != family], [r for r in rows if r.family == family], deadlines)
    out["historical_dijkstra"] = report(rows, history, deadlines)
    if prospective is not None:
        reg = read(prospective / "registration.json")
        plan = read(prospective / "frozen.json")
        require, sha = tools["require"], tools["sha"]
        require(sha((prospective / "registration.json").read_bytes()) ==
                "861bbe6468eb27d15730ac02359d9a3f68a78e1ac8007282b566daaf7c29d0a1" and
                sha((prospective / "frozen.json").read_bytes()) == reg["payload"]["plan.json"],
                "different prospective graph experiment")
        targets = list(modern(prospective, plan, reg, "1c4c3018964f717c4fac8098613af29b32ce927a",
                              dict.fromkeys(plan["protocol"]["groups"], "layered_dijkstra"), tools, penalty, weights))
        out["layered_without_dijkstra"] = report(rows, targets, deadlines, prospective=True)
        out["layered_with_dijkstra"] = report(rows + history, targets, deadlines, prospective=True)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--prospective", type=Path, help="registered two-boot layered Dijkstra study directory")
    parser.add_argument("--verify", type=Path)
    args = parser.parse_args()
    result = replay(args.prospective)
    if args.verify:
        if result != read(args.verify):
            raise SystemExit("published interval replay differs")
        print("Exact interval replay matched all published cases and deadlines.")
    else:
        print(json.dumps(result, indent=2))
