"""Project the fixed version-9001 divider research stream using canonical receipt checks."""
from pathlib import Path
import runpy
from tempfile import TemporaryDirectory

ROOT = Path(__file__).resolve().parents[2]
READER = runpy.run_path(str(ROOT / "collect.py"))
PRIOR = runpy.run_path(str(ROOT / "experiments/rust_operand_bytes/collect.py"))
VERSION = 9001


def production(records, pid):
    # Preserve the original archive and enforce the ordinary reader's explicit
    # rejection. Only translate the fixed start capability in the private copy.
    if type(pid) is not int or not 0 < pid < (1 << 31):
        raise ValueError("invalid research PID")
    name = f"process-{pid}.jsonl"
    if [p.name for p in records.iterdir()] != [name]:
        raise ValueError("expected exactly one raw divide-clock stream")
    ordinary = READER["collect"](records, pid)
    if ordinary.get("valid") is not False or ordinary.get("reason") != "unsupported latency bound capability":
        raise ValueError("ordinary reader did not reject the experimental capability")
    raw = (records/name).read_bytes()
    marker = b'"latency_bound_version":9001,'
    start = raw.partition(b'\n')[0]
    if not (start.count(marker) == raw.count(marker) == 1):
        raise ValueError("expected one experimental marker in the first record")
    with TemporaryDirectory() as tmp:
        folder = Path(tmp)
        (folder/name).write_bytes(raw.replace(marker, b'"latency_bound_version":1,', 1))
        result = PRIOR["production"](folder, pid, "operand_bytes", READER["collect"])
    result["latency_bound"] = {**result["latency_bound"], "version": VERSION}
    return result
