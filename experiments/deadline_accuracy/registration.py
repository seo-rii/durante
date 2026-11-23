"""Read the registered bytes and materialize the exact inputs used by the collector and replayer."""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ARMS = {"near-three": "gate", "exact-one": "exact", "warm-three": "warm", "interleaved-three": "warm"}


INTERLEAVED_STUDIES = ("contest_batch", "contest_scale")


def registration_for(strategy, contest=False, *, study=None):
    expand = None
    prefix = ""
    if study is not None and (strategy != "interleaved-three" or contest or study not in INTERLEAVED_STUDIES):
        raise ValueError("unregistered interleaved study")
    if strategy == "interleaved-three" and not contest:
        if study == "contest_scale":
            from contest_scale.corpus import expand
        else:
            from contest_batch.corpus import expand
        directory = HERE.parent / (study or "contest_batch")
    elif strategy == "warm-three" and not contest:
        from contest_warm.corpus import expand
        directory = HERE.parent / "contest_warm"
    elif contest and strategy == "exact-one":
        from contest_accuracy.corpus import expand
        directory = HERE.parent / "contest_accuracy"
    elif not contest and strategy in ("near-three", "exact-one"):
        directory = HERE
        prefix = "exact-" if strategy == "exact-one" else ""
    else:
        raise ValueError("unregistered combination of study and calibration strategy")
    raw_plan = (directory / f"{prefix}plan.json").read_bytes()
    raw_rules = (directory / f"{prefix}rules.json").read_bytes()
    plan = json.loads(raw_plan)
    if expand is not None:
        expand(plan)
    return raw_plan, raw_rules, plan


def load_registration(exact, contest=False):
    return registration_for("exact-one" if exact else "near-three", contest)
