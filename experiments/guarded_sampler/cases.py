"""Append the two controlled mixed-path inputs to the frozen first-run corpus."""
import argparse
import hashlib
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = [(HERE.parent / "first_run_sampler/cases.json", "0bf24c2e195846f7f912b2a0c85f3c9ff537b62da1c088c5e8472d305a519ef7"),
           (HERE / "control/cases.json", "004aa569e05299be6071d116f17d83323a6895a1676051c842ae8594a36d9c45")]


def manifest():
    cases = []
    for path, digest in SOURCES:
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"changed source cases: {path}")
        cases.extend(json.loads(data))
    return cases


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true")
    write = parser.parse_args().write
    raw = (json.dumps(manifest(), indent=2) + "\n").encode()
    out = HERE / "cases.json"
    if write:
        out.write_bytes(raw)
    elif out.read_bytes() != raw:
        raise SystemExit("case manifest differs")
    print(hashlib.sha256(raw).hexdigest())
