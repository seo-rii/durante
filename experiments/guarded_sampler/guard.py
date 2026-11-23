"""Choose a diagnostic sampling result using the observed fraction of eligible scalar reads."""
import argparse
import json
from pathlib import Path

U64_MAX = (1 << 64) - 1
COUNT_FIELDS = ("loads", "pairs", "near_pairs", "far_pairs")
THRESHOLD = 90


def check(condition, message):
    if not condition:
        raise ValueError(message)


def counter(value):
    return type(value) is int and 0 <= value <= U64_MAX


def read_report(path):
    def unique(items):
        obj = dict(items)
        check(len(items) == len(obj), "duplicate report field")
        return obj
    return json.loads(path.read_bytes(), object_pairs_hook=unique)


def decision(sample, guard, pid):
    """Use only on a verified, normally completed, single-threaded guarded launch."""
    check(type(pid) is int and 0 < pid < 2**32, "invalid expected PID")
    identity = {"pid": pid, "threads": 1}
    check(isinstance(guard, dict) and set(guard) == identity.keys() | {"schema", "arm", "scope", "omitted_loads"}
          and guard["arm"] == "guarded" and guard["scope"] == "main_module_eligible_scalar_reads",
          "wrong guard receipt or scope")
    check(all(counter(guard[k]) and guard[k] == n for k, n in {**identity, "schema": 1}.items())
          and counter(guard["omitted_loads"]), "invalid guard identity or count")
    header = {**identity, "schema": 2, "period": 1024, "line_bytes": 64}
    check(isinstance(sample, dict) and set(sample) == header.keys() | set(COUNT_FIELDS) | {"sites"}
          and all(counter(sample[k]) and sample[k] == n for k, n in header.items()), "wrong selected report")
    sites = sample["sites"]
    check(isinstance(sites, list) and len(sites) <= 64, "invalid selected site list")
    total = dict.fromkeys(COUNT_FIELDS, 0)
    previous = -1
    for site in sites:
        check(isinstance(site, dict) and set(site) == set(COUNT_FIELDS) | {"offset", "sampled_address_span_bytes"}
              and all(counter(v) for v in site.values()), "invalid selected site")
        offset, loads, pairs = site["offset"], site["loads"], site["pairs"]
        periods, remainder = divmod(loads, 1024)
        check(offset > previous and loads > 0 and pairs == periods + (remainder >= 2)
              and pairs == site["near_pairs"] + site["far_pairs"]
              and (loads != 1 or site["sampled_address_span_bytes"] == 0), "invalid site order, cadence or span")
        previous = offset
        for k in COUNT_FIELDS:
            total[k] += site[k]
    check(all(counter(sample[k]) and sample[k] == total[k] for k in COUNT_FIELDS), "invalid selected totals")
    selected, omitted = total["loads"], guard["omitted_loads"]
    eligible = selected + omitted
    check(eligible <= U64_MAX, "combined read count overflow")
    sufficient = eligible > 0 and 100 * selected >= THRESHOLD * eligible
    return {"schema": 1, "pid": pid, "selected_visits": selected, "omitted_visits": omitted,
            "total_visits": eligible, "minimum_coverage_percent": THRESHOLD,
            "status": "use_profiled" if sufficient else "run_full"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pid", type=int, required=True, help="PID from the trusted launcher")
    parser.add_argument("--sample", type=Path, required=True)
    parser.add_argument("--guard", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(decision(read_report(args.sample), read_report(args.guard), args.pid)))
