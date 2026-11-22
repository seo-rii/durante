"""Register the cold inputs, two balanced cost blocks and three guarded recovery audits."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ARMS = ("live", "full", "profiled", "guarded")
AUDITS = ("path_switch/same", "path_switch/switch", "path_mix/shift")
FIRST = (0, 1, 3, 2)


def build(cases):
    schedule = [dict(case_id=c["case_id"], rep=0, arm="calibration" if c["program"] == "path_mix" else "confirmation")
                for c in cases if c["split"] == "calibration"]
    held = [c["case_id"] for c in cases if c["split"] == "holdout"]
    for rep in range(8):
        for position in range(len(held)):
            i = (position + rep) % len(held)
            for k in FIRST:
                schedule.append(dict(case_id=held[i], rep=rep + 1, arm=ARMS[(k + i + rep) % 4]))
    for case_id in AUDITS:
        if case_id not in held:
            raise ValueError("missing audit fixture: " + case_id)
        schedule.append(dict(case_id=case_id, rep=0, arm="audit_guarded"))
        schedule.append(dict(case_id=case_id, rep=0, arm="audit_fallback", if_run_full=True))
    return schedule


def effective_schedule(plan, recovery):
    if set(recovery) != set(AUDITS):
        raise ValueError("missing or unexpected recovery audits")
    result = []
    for item in plan["schedule"]:
        if item.get("if_run_full"):
            status = recovery[item["case_id"]]["decision"]["status"]
            if status not in ("use_profiled", "run_full"):
                raise ValueError("invalid recovery decision")
            if status == "use_profiled":
                continue
        result.append({k: v for k, v in item.items() if k != "if_run_full"})
    return result


def main():
    names = ["bench.py", "schedule.py", "profile.py", "guard.py", "guest.sh", "cases.json", "design.json",
             "production_reader.py", "base/bench.py", "cost_bench.py", "first_bench.py",
             "v11.so", "span.so", "profiled.so", "guarded.so"]
    for group in ("bin", "dynamorio", "profiles"):
        names.extend(p.relative_to(ROOT).as_posix() for p in (ROOT / group).rglob("*") if p.is_file())
    files = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in sorted(names)}
    plan = dict(schema=1, cpu=1, work_budget=100_000_000_000, poll_interval_ms=5, trial_timeout_s=120,
                tools_sha256=files, schedule=build(json.loads((ROOT / "cases.json").read_bytes())))
    (ROOT / "protocol.json").write_text(json.dumps(plan, indent=2) + "\n")


if __name__ == "__main__":
    main()
