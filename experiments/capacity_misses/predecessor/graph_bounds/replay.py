"""Reconstruct relative-time witnesses directly from the predecessor and graph observations."""
from collections import Counter, defaultdict
from fractions import Fraction
import json
from pathlib import Path
import statistics
import sys

HERE = Path(__file__).resolve().parent
PRED = HERE.parent
CAPACITY = PRED.parent
FLAT = CAPACITY / "flat_cache"
sys.path.insert(0, str(FLAT))
sys.path.insert(0, str(CAPACITY))
from flat_cache import replay as graph

Q = Fraction
require = graph.require
KEYS = ("latency_x16", "rep_bytes", "l1d_misses", "random_2m", "random_32m",
        "stream_2m", "l1d_dependent", "dependent_2m")
WITNESS = (1, 0, 0, 0, 0, 0, 700, 0)


def read(path):
    return json.loads(path.read_bytes(), parse_float=Q)


def observations():
    plan = read(PRED / "frozen.json")
    registration = read(PRED / "registration.json")
    registration["payload"] = {f["path"]:f["sha256"] for f in registration["files"]}
    hosts = read(PRED / "evidence/cloud.json")
    graph_reg, graph_plan = graph.registered()
    clients = {a:graph_plan["clients"][n] for a,n in graph_plan["protocol"]["clients"].items()}
    graph_hosts = read(FLAT / "evidence/cloud.json")
    counts, native = defaultdict(list), defaultdict(list)
    for boot in graph.PHASES:
        archive = PRED / "evidence" / (boot + ".tar.gz")
        data, _ = graph.milan.read_boot(archive, boot, plan, registration, hosts[boot])
        for case, samples in data.items():
            native[case].extend(samples["native"])
            for row in samples["probe"]:
                fields = graph.milan.features(row)
                counts[case].append(tuple(fields[k] for k in KEYS))
        data, _ = graph.read_boot(boot, graph_hosts[boot], graph_reg, graph_plan, evidence=FLAT/"evidence",
                                  freeze=graph.FREEZE, client_by_arm=clients,
                                  diagnostic_by_arm={"outer":"outer", "flat_outer":"outer"})
        for case, arms in data.items():
            native[case].extend(r["cpu_us"] for r in arms["native"])
            for arm in ("outer", "flat_outer"):
                for row in arms[arm]:
                    fields = graph.ml4(row["counters"], KEYS)
                    counts[case].append(tuple(fields[k] for k in KEYS))
    for case, vectors in counts.items():
        totals = (14, 4) if case in plan["cases"] else (10, 20)
        require((len(native[case]), len(vectors)) == totals and len(set(vectors)) == 1,
                "incomplete or variable observations: " + case)
    frozen = plan["weights"]["uniform"]["ML4"]
    require(frozen == plan["weights"]["clustered"]["ML4"], "different frozen weights")
    return {case:v[0] for case,v in counts.items()}, native, tuple(frozen[k] for k in KEYS)


def dot(a, b):
    return sum(x*y for x,y in zip(a, b, strict=True))


def evaluate(counts, native, weights):
    score = {case:dot(row, weights) for case,row in counts.items()}
    require(min(score.values()) > 0 and min(weights) >= 0, "invalid nonnegative clock")
    outcomes = Counter()
    misses = []
    for case, times in native.items():
        problem, _, solver = case.partition("-")
        reference = problem + "-dsu" if solver else "reference"
        if case == reference:
            continue
        anchor = native[reference]
        for factor in (Q(1), Q(5, 4), Q(3, 2), Q(2), Q(3)):
            if max(times) <= factor * min(anchor):
                passes = True
            elif min(times) > factor * max(anchor):
                passes = False
            else:
                continue
            ok = (score[case] <= factor * score[reference]) == passes
            group = ("predecessor registered" if solver and factor > 1 else
                     "predecessor parity" if solver else "graph multipliers")
            outcomes[group, ok] += 1
            if not ok:
                misses.append((case, str(factor)))
        if not solver:
            require(max(times) <= 65_000, "changed native 65 ms outcome")
            ok = max(anchor) * score[case] <= 65_000 * score[reference]
            outcomes["graph 65 ms", ok] += 1
            if not ok:
                misses.append((case, "65 ms"))
    return outcomes, misses


def certificate(counts, native):
    d, f, m = (counts["uniform-" + x] for x in ("dsu", "fenwick", "multiset"))
    b, r = counts["block8"], counts["reference"]
    require(10*max(native["uniform-fenwick"]) < 9*min(native["uniform-dsu"]) and
            10*max(native["uniform-multiset"]) < 17*min(native["uniform-dsu"]) and
            min(native["block8"]) > 3*max(native["reference"]), "different native endpoints")
    # Ten times [10(0.9D - F) + (1.7D - M) + (B - 3R)].
    vector = tuple(107*dv - 100*fv - 10*mv + 10*bv - 30*rv
                   for dv,fv,mv,bv,rv in zip(d, f, m, b, r, strict=True))
    require(max(vector) < 0, "certificate has a nonnegative coordinate")
    return vector


def main():
    counts, native, frozen = observations()
    for problem in ("uniform", "clustered"):
        f, d = (problem + "-" + x for x in ("fenwick", "dsu"))
        print(problem, "native median difference (ms)", (statistics.median(native[f])-statistics.median(native[d]))/1000)
        terms = [(fv-dv)*w*1000 for fv,dv,w in zip(counts[f], counts[d], frozen, strict=True)]
        print("ML4 score difference (ms)", float(sum(terms)))
        for k,x in zip(KEYS, terms, strict=True):
            print(" ", k, str(x), f"({float(x):+.9f} ms)")
    tripled = tuple(3*w if k == "l1d_dependent" else w for k,w in zip(KEYS, frozen, strict=True))
    for name, weights in (("frozen ML4", frozen), ("triple dependent L1D", tripled), ("integer witness", WITNESS)):
        passed, misses = evaluate(counts, native, weights)
        print(name, sorted(passed.items()), "misses", misses)
    checks, _ = evaluate(counts, native, WITNESS)
    require(sum(checks.values()) == sum(k for (_, ok),k in checks.items() if ok) == 31, "witness failed")
    print("certificate", certificate(counts, native))


if __name__ == "__main__":
    main()
