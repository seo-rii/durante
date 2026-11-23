"""Register the warm-calibration cohort and reproduce its independent input/output oracles."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_accuracy import money, wall
from contest_accuracy.corpus import sha
from contest_warm import dreaming, posterize, scrivener

GENERATORS = {"dreaming": dreaming, "posterize": posterize, "scrivener": scrivener,
              "wall": wall, "money": money}
CONTROLS = {"wall": "crossed_intervals", "money": "dense_scrambled"}


def register(binaries):
    cases = []
    for program, module in GENERATORS.items():
        control = program in CONTROLS
        variants = (CONTROLS[program],) if control else module.CASES
        binary_sha = sha((binaries / program).read_bytes())
        for name in variants:
            stdin, stdout = module.make(name)
            fixed = [5, 12, 25, 50]
            if program == "money":
                fixed = [5, 6, 12, 25]
            cases.append(dict(case_id=f"{program}/{name}", program=program, variant=name, split="holdout",
                              cohort="previous" if control else "prospective", binary_sha256=binary_sha,
                              stdin_sha256=sha(stdin.encode()), stdout_sha256=sha(stdout.encode()), fixed_limits_ms=fixed))
    probe = json.loads((HERE / "rules.json").read_bytes())["probe_sha256"]
    return dict(schema=1, authority="diagnostic_only", poll_ms=5, timeout_ms=120000, native_rounds=11,
                probe_sha256=probe, deadline_percent=[85, 100, 115], cases=cases)


def expand(plan):
    from contest_accuracy.corpus import expand as expand_cases
    expand_cases(plan, GENERATORS)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binaries", type=Path, required=True)
    print(json.dumps(register(parser.parse_args().binaries), indent=2))
