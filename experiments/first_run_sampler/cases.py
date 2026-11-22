"""Reproduce the study cases from Aonohako #154 and the path-switch control."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
UPSTREAM_SHA = "809861a02942a7c54dfcc322764b9c39ebee3eb8388756854855ea1d5b0f9e58"
PROGRAMS = {"integer": (1, 2, 4), "branch": (1, 2, 4), "rep_fill": (1, 2, 4),
            "mod_inverse": (1, 2), "branch_scan": (1, 2)}


def manifest(corpus, control):
    original = corpus.read_bytes()
    if hashlib.sha256(original).hexdigest() != UPSTREAM_SHA:
        raise ValueError("different Aonohako #154 case manifest")
    by_id = {c["case_id"]: c for c in json.loads(original)}
    cases = []
    for program, scales in PROGRAMS.items():
        for scale in scales:
            row = by_id[f"{program}/{scale}"]
            cases.append({k: row[k] for k in ("case_id", "program", "binary_sha256", "stdin", "stdout")} |
                         {"stdin_sha256": row["input_sha256"], "stdout_sha256": row["expected_sha256"],
                          "split": "calibration" if scale == 1 else "holdout", "population": "real"})
    for row in json.loads(control.read_bytes()):
        cases.append({**row, "split": "calibration" if row["case_id"] == "path_switch/train" else "holdout",
                      "population": "control"})
    for c in cases:
        for field in ("stdin", "stdout"):
            if hashlib.sha256(c[field].encode()).hexdigest() != c[field + "_sha256"]:
                raise ValueError(f"{c['case_id']}: invalid {field} digest")
    if len(cases) != 16 or len(set(c["binary_sha256"] for c in cases)) != 6:
        raise ValueError("expected six ELF identities, each with a calibration and held-out inputs")
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path, help="Aonohako #154 corpus/cases.json")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    out = HERE / "cases.json"
    value = (json.dumps(manifest(args.corpus, HERE / "control/cases.json"), indent=2) + "\n").encode()
    if args.write:
        out.write_bytes(value)
    elif value != out.read_bytes():
        raise SystemExit("different frozen cases")
    print(hashlib.sha256(value).hexdigest())
