"""Read the registered bytes and materialize the exact inputs used by the collector and replayer."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent


def load_registration(exact, contest=False):
    if contest:
        from contest_accuracy.corpus import expand
        if not exact:
            raise ValueError("the contest study registered the exact-one strategy")
        directory = HERE.parent / "contest_accuracy"
        prefix = ""
    else:
        directory = HERE
        prefix = "exact-" if exact else ""
    raw_plan = (directory / f"{prefix}plan.json").read_bytes()
    raw_rules = (directory / f"{prefix}rules.json").read_bytes()
    plan = json.loads(raw_plan)
    if contest:
        expand(plan)
    return raw_plan, raw_rules, plan
