"""Compare each registered fresh or cached execution with Aonohako's input-deadline CLI."""
import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import require
from preflight_cache.verify import verify
from shape_calibration import aonohako_clock


def replay(root, binaries, reference):
    verify(root)
    plan = json.loads((HERE / "plan.json").read_bytes())
    cases = {c["case_id"]: c for c in plan["cases"]}
    summary = json.loads((root / "summary.json").read_bytes())
    counts = {name: Counter() for name in ("fresh", "cold", "warm")}
    projections = {}
    with tempfile.TemporaryDirectory() as tmp:
        for row in summary["rows"]:
            case = cases[row["case_id"]]
            prepared = row["prepared"]
            capture = root / prepared["capture"]
            elf = binaries / case["program"]
            stdin = capture / "full/stdin"
            limits = tuple(e["limit_ms"] for e in row["limits"])
            key = (capture, limits)
            if key not in projections:
                receipt = aonohako_clock.export(root / prepared["selection"], capture, root / prepared["policy"],
                                                elf, stdin, limits)
                dest = Path(tmp) / f"{len(projections)}.json"
                dest.write_text(json.dumps(receipt))
                projections[key] = dest
            arm = "fresh" if row["arm"] == "fresh" else ("warm" if prepared["hit"] else "cold")
            for i, ms in enumerate(limits):
                wrapped = root / "trials" / f"{row['sequence']:03d}" / f"limit-{i}/result.json"
                observed = json.loads(wrapped.read_bytes())
                command = [reference, "replay-input-deadline", "--projection", projections[key], "--live", wrapped,
                           "--elf", elf, "--stdin", stdin, "--limit-ms", str(ms)]
                proc = subprocess.run(command, check=True, stdout=subprocess.PIPE)
                expected = {k: observed[k] for k in ("schema", "authority", "selection_sha256", "policy_sha256",
                                                       "limit_ms", "pid", "status")}
                expected["exceeded"] = observed["status"] == "budget_exceeded"
                require(json.loads(proc.stdout) == expected, f"unexpected replay of {case['case_id']} at {ms} ms")
                counts[arm][observed["status"]] += 1
    return counts


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("output", type=Path, help="extracted output directory")
    cli.add_argument("--binaries", required=True, type=Path, help="registered static workload executables")
    cli.add_argument("--reference", required=True, type=Path, help="Aonohako durante-reference executable")
    args = cli.parse_args()
    print(json.dumps(replay(args.output, args.binaries, args.reference.resolve()), indent=2))
