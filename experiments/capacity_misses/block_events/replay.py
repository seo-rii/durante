"""Check static block-count receipts and intersect coefficient constraints in ns/event."""
import argparse
from collections import Counter, defaultdict
from dataclasses import dataclass
from fractions import Fraction
import gzip
import importlib.util
import json

from collect_events import CONTROL, EVENTS, HERE, PANELS, checked_row, digest, registration, require

spec = importlib.util.spec_from_file_location("milan_archive", HERE.parent / "milan" / "replay.py")
milan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(milan)
PHASES = ("primary", "repeat", "pooled")
TARGET = "milan/primary/dijkstra--set_decrease_key/2"
REG_HASH = dict(milan="3a65e6329eda2ec5a1d8bf1a1e248f8ea953c7b71d380d44daf124518b15c613",
                aonohako_shortest_paths="c129606b16bfc46e393ad3bc5f9f296485918d0ba939a6219bd862bcc951a706")


def read(path):
    return json.loads(path.read_bytes())


def counts(observed, plan, panel):
    require(observed["plan_sha256"] == registration(panel)["plan.json"], "different guest plan")
    require(checked_row(observed["control"], milan.sha(b"")) == CONTROL, "incorrect assembly control")
    samples = defaultdict(list)
    names = list(plan["cases"])
    schedule = [(r, n) for r, ordered in enumerate((names, names[::-1])) for n in ordered]
    for (rep, name), row in zip(schedule, observed["rows"], strict=True):
        require((row["rep"], row["case"]) == (rep, name), "wrong counter schedule")
        ev = checked_row(row, plan["cases"][name]["expected_sha256"])
        ev["calls"] = ev["direct"] + ev["indirect"]
        samples[name].append(ev)
    return samples


def guest(panel):
    path = HERE.parent / panel
    require(digest(path / "registration.json") == REG_HASH[panel], "changed guest registration")
    reg = read(path / "registration.json")
    reg["payload"] = registration(panel)
    require(digest(path / "frozen.json") == reg["payload"]["plan.json"], "changed guest plan")
    plan = read(path / "frozen.json")
    cloud = read(path / "evidence/cloud.json")
    boots = {p: milan.read_boot(path / "evidence" / (p + ".tar.gz"), p, plan, reg, cloud[p])[0]
             for p in ("primary", "repeat")}
    boots["pooled"] = {n: {f: boots["primary"][n][f] + boots["repeat"][n][f] for f in ("native", "probe")}
                       for n in plan["cases"]}
    return plan, boots


@dataclass(frozen=True)
class Cell:
    name: str
    native: str
    # Signed lower and upper differences from the deadline: intercept + k*slope.
    low: tuple
    high: tuple

    def forecast(self, k):
        if self.high[0] + k * self.high[1] <= 0:
            return "accept"
        if self.low[0] + k * self.low[1] > 0:
            return "timeout"
        return "indeterminate"


def bounds(values):
    return min(values), max(values)


def cells(panel, plan, boots, counts, event):
    graph = panel == "aonohako_shortest_paths"
    proto = plan["protocol"]
    if graph:
        require(proto["programs"][0] == proto["reference"], "different 65 ms reference")
    groups = {"graph": proto["programs"]} if graph else proto["groups"]
    for phase, samples in boots.items():
        for group, (anchor, *solvers) in groups.items():
            weights = plan["ml4"] if graph else plan["weights"][group]["ML4"]
            weights = {k: 10**9 * Fraction(str(v)) for k, v in weights.items()}
            scores = {}
            for n in (anchor, *solvers):
                scores[n] = bounds([milan.predict(weights, milan.features(p)) for p in samples[n]["probe"]])
            def event_bounds(n):
                return bounds([v[event] for v in counts[n]])
            al, ah = scores[anchor]
            eal, eah = event_bounds(anchor)
            ref = samples[anchor]["native"]
            for n in solvers:
                sl, sh = scores[n]
                esl, esh = event_bounds(n)
                ns = samples[n]["native"]
                for factor in ([proto["deadline_us"]] if graph else proto["factors"]):
                    if graph:
                        qlow, qhigh = Fraction(factor, max(ref)), Fraction(factor, min(ref))
                        native = milan.relative_verdict(ns, [1], factor)
                    else:
                        qlow = qhigh = Fraction(str(factor))
                        native = milan.relative_verdict(ns, ref, qlow)
                    yield Cell(f"{panel}/{phase}/{n}/{factor:g}", native,
                               (sl - qhigh*ah, esl - qhigh*eah), (sh - qlow*al, esh - qlow*eal))


@dataclass
class Interval:
    lower: Fraction = Fraction(0)
    lower_strict: bool = False
    lower_cause: str = "nonnegative"
    upper: Fraction | None = None
    upper_strict: bool = False
    upper_cause: str | None = None
    constant_failure: str | None = None

    def constrain(self, cell):
        if cell.native == "indeterminate":
            return
        if cell.native == "accept":
            a, b = cell.high
            strict = False
        else:
            a, b = (-x for x in cell.low)
            strict = True
        # Wanted a + b*k <= 0 (accept) or < 0 (timeout).
        if not b:
            if a > 0 or (a == 0 and strict):
                self.constant_failure = cell.name
            return
        edge = Fraction(-a, b)
        if b < 0 and (edge > self.lower or (edge == self.lower and strict and not self.lower_strict)):
            self.lower, self.lower_strict, self.lower_cause = edge, strict, cell.name
        elif b > 0 and (self.upper is None or edge < self.upper or
                        (edge == self.upper and strict and not self.upper_strict)):
            self.upper, self.upper_strict, self.upper_cause = edge, strict, cell.name

    @property
    def feasible(self):
        return self.constant_failure is None and (self.upper is None or self.upper > self.lower or
                 (self.upper == self.lower and not (self.lower_strict or self.upper_strict)))

    def choose(self):
        require(self.feasible, "cannot select from empty interval")
        if self.upper is not None:
            return (self.lower + self.upper) / 2
        if self.lower_strict and self.lower == 0:
            return Fraction(1)
        return 2 * self.lower

    def report(self):
        def endpoint(v, strict, cause):
            return None if v is None else dict(ns_per_event=round(float(v), 8), exact=str(v), strict=strict, cell=cause)
        return dict(feasible=self.feasible,
                    lower=endpoint(self.lower, self.lower_strict, self.lower_cause),
                    upper=endpoint(self.upper, self.upper_strict, self.upper_cause), constant_failure=self.constant_failure)


def intersect(cells):
    allowed = Interval()
    for c in cells:
        allowed.constrain(c)
    return allowed


def comparisons(cells, coefficient):
    by_phase = defaultdict(Counter)
    errors = {}
    for cell in cells:
        phase = cell.name.split("/")[1]
        forecast = cell.forecast(coefficient)
        verdict = ("indeterminate" if "indeterminate" in (cell.native, forecast) else
                   "agree" if cell.native == forecast else "false_" + forecast)
        by_phase[phase][verdict] += 1
        if verdict.startswith("false_"):
            errors[cell.name] = verdict
    return dict(outcomes=by_phase, errors=errors)


def replay(detailed=False):
    local = json.loads(gzip.decompress((HERE / "local.json.gz").read_bytes()))
    require(local["schema"] == 1 and local["source_sha256"] == digest(HERE / "probe.c") and
            set(local["panels"]) == set(PANELS), "unexpected local evidence")
    all_counts, groups, deltas = {}, {}, {}
    transfer = PANELS[1]
    for panel in PANELS:
        plan, boots = guest(panel)
        rec = counts(local["panels"][panel], plan, panel)
        all_counts[panel] = {n: {k: bounds([x[k] for x in sample]) for k in (*EVENTS, "instructions")}
                             for n, sample in rec.items()}
        deltas[panel] = {}
        for name, pair in rec.items():
            gce = [v["instructions"] for v in boots["pooled"][name]["probe"]]
            deltas[panel][name] = round(max(10**6 * abs(s["instructions"] - i) / i for s in pair for i in gce), 3)
        groups[panel] = {e: list(cells(panel, plan, boots, rec, e)) for e in EVENTS}
    report = dict(local_ranges=all_counts, max_local_guest_instruction_delta_ppm=deltas, baseline={}, candidates={})
    for p in PANELS:
        report["baseline"][p] = comparisons(groups[p]["direct"], 0)
    for e in EVENTS:
        train, holdout = groups["milan"][e], groups[transfer][e]
        target, = (c for c in train if c.name == TARGET)
        require(target.native == "timeout" and target.forecast(0) == "accept", "missing primary target")
        intervals = {"target": [target], "training": train, "holdout": holdout,
                     "target_and_holdout": [target, *holdout], "all": train + holdout}
        result = {k: intersect(v).report() for k, v in intervals.items()}
        trained = intersect(train)
        if trained.feasible:
            k = trained.choose()
            result.update(selected_ns_per_event=float(k), training_at_k=comparisons(train, k),
                          holdout_at_k=comparisons(holdout, k))
        elif intersect([target]).feasible:
            # Post-hoc severity example only: use the smallest whole ns clearing the target.
            target_range = intersect([target])
            k = target_range.lower.numerator // target_range.lower.denominator + 1
            require(target.forecast(k) == "timeout", "illustrative coefficient missed target")
            result["target_only_example"] = dict(ns_per_event=k, training=comparisons(train, k),
                                                 holdout=comparisons(holdout, k))
        if detailed:
            result["cells"] = {c.name: (c.native, intersect([c]).report()) for c in train + holdout}
        report["candidates"][e] = result
    return report


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--cells", action="store_true", help="include the interval imposed by each individual verdict")
    cli.add_argument("--verify", action="store_true", help="compare against the committed summary")
    args = cli.parse_args()
    output = json.dumps(replay(args.cells), indent=2) + "\n"
    if args.verify:
        require(not args.cells and output == (HERE / "results.json").read_text(), "results differ")
        print("Verified 32 counter trials, two assembly controls and four archived Milan boots.")
    else:
        print(output, end="")
