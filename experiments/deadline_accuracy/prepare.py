"""Build a guest payload for one of the preregistered deadline accuracy studies."""
import argparse
import json
from pathlib import Path
import shutil
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from calibrated_limits.prepare import digest, prepare


def package(out, runtime, mode, probe=None, candidate=None):
    if mode == "parity":
        expected = json.loads((HERE / "client-parity.json").read_bytes())["client_sha256"]
        if candidate is None or digest(candidate) != expected["candidate"] or digest(runtime / "v11.so") != expected["released"]:
            raise ValueError("expected the registered candidate and released client")
        prepare(out, runtime, study=HERE, benchmark="client_parity.py")
        shutil.copyfile(candidate, out / "candidate.so")
        with (out / "checksums.sha256").open("a") as checks:
            checks.write(f"{expected['candidate']}  candidate.so\n")
        return
    if probe is None:
        raise ValueError("a registered temporal probe is required")
    if mode == "exact":
        # The common packager builds the binaries listed in the primary registration.
        # Both registrations must bind every program to the same ELF and probe.
        primary, exact = (json.loads((HERE / name).read_bytes()) for name in ("plan.json", "exact-plan.json"))
        def identities(plan):
            return {(c["program"], c["binary_sha256"]) for c in plan["cases"]}
        if identities(primary) != identities(exact) or primary["probe_sha256"] != exact["probe_sha256"]:
            raise ValueError("confirmation requires a different payload")
    benchmark = "bench_exact.py" if mode == "exact" else "bench.py"
    prepare(out, runtime, study=HERE, probe=probe, benchmark=benchmark)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("out", type=Path)
    cli.add_argument("--runtime", type=Path, required=True)
    cli.add_argument("--mode", choices=("primary", "exact", "parity"), default="primary")
    cli.add_argument("--probe", type=Path)
    cli.add_argument("--candidate", type=Path)
    args = cli.parse_args()
    package(args.out, args.runtime.resolve(), args.mode, args.probe, args.candidate)
