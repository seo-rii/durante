"""Prepare interleaved native calibration packets for a batch of trusted static executables and inputs."""
import argparse
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import re
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from deadline_accuracy import gate
from first_run_sampler.profile import elf_digest, read_json, require, sha
from guarded_native import anchor
from shape_calibration import run, select_policy, temporal


@dataclass
class Input:
    case_id: str
    elf: Path
    stdin: Path
    capture: Path
    policy: bytes
    observation: dict
    context: dict


def bind(item, placement):
    case_id = item["id"]
    elf, stdin, capture = (Path(item[key]).resolve() for key in ("elf", "stdin", "capture"))
    raw = (capture / "policy.json").read_bytes()
    source = read_json(raw)
    clock = anchor.clock_from_raw(raw, anchor.static_elf(elf))
    selection_raw = (capture / "baseline-selection.json").read_bytes()
    selection = read_json(selection_raw)
    require(selection["method"] == "footprint" and selection["action"] == "calibrate",
            "expected the current input's paid footprint selection")
    full, order = select_policy.profiles(capture, clock.elf_sha256, temporal.PROBE_SHA, "footprint")
    run.check_selection(selection, full, order, source, clock)
    observation = full["observation"]
    require(elf_digest(stdin) == observation["stdin_sha256"], "requested stdin differs from the capture")
    require(gate.original_is_current(source, observation, placement), "first observation must match capture, CPU and boot")
    context = dict(**placement, source_selection_sha256=sha(selection_raw), source_policy_sha256=clock.policy_sha256,
                   elf_sha256=clock.elf_sha256, stdin_sha256=observation["stdin_sha256"],
                   current_result_sha256=observation["result_sha256"])
    return Input(case_id, elf, stdin, capture, raw, observation, context)


def rounds(ids):
    ordered = sorted(ids, key=lambda name: sha(name.encode()))
    return [ordered[r % len(ordered):] + ordered[:r % len(ordered)] for r in range(3)]


def manifest(directory, inputs, schedule, placement, *, creating):
    cases = []
    seen = {item.observation["pid"] for item in inputs.values()}
    require(len(seen) == len(inputs), "duplicate capture PID")
    for name, item in inputs.items():
        packet = directory / name
        receipt, selection = gate.packet_evidence(packet, item.observation, item.context, item.policy, "warm-three")
        require(receipt["reused_original"], "batch must use the original paid observation")
        for index in range(4):
            native = read_json((packet / f"policy-{index}.json").read_bytes())["native"]
            require(native["pid"] not in seen, "duplicate capture or native PID in batch")
            seen.add(native["pid"])
        for path, expected in ((packet / "packet.json", receipt), (packet / "selection.json", selection)):
            if creating:
                anchor.save_new(path, expected)
            else:
                require(gate.exact_json(read_json(path.read_bytes()), expected), "batch packet differs from its evidence")
        cases.append(dict(id=name, elf_sha256=item.context["elf_sha256"], stdin_sha256=item.context["stdin_sha256"],
                          current_result_sha256=item.context["current_result_sha256"],
                          source_selection_sha256=item.context["source_selection_sha256"],
                          source_policy_sha256=item.context["source_policy_sha256"],
                          packet_sha256=elf_digest(packet / "packet.json"), selection_sha256=elf_digest(packet / "selection.json")))
    return dict(schema=1, authority="diagnostic_only", strategy="interleaved-warm-three",
                placement=placement, cases=cases, rounds=schedule)


def prepare(items, out):
    """Publish one complete batch or verify its immutable packets against the ordered current captures."""
    require(isinstance(items, list) and len(items) >= 2, "batch requires at least two inputs")
    ids = [i["id"] for i in items]
    require(all(isinstance(s, str) and re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*", s) for s in ids),
            "invalid batch case ID")
    paths = {Path(name) for name in ids}
    require(len(paths) == len(items) and all(paths.isdisjoint(p.parents) for p in paths),
            "duplicate or overlapping batch case IDs")
    out = Path(out)
    placement = gate.placement()
    # Lock the parent while checking or creating the complete group, so simultaneous requests pay at most once.
    descriptor = os.open(out.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        inputs = {i["id"]: bind(i, placement) for i in items}
        require(len({(i.context["elf_sha256"], i.context["stdin_sha256"]) for i in inputs.values()}) == len(inputs),
                "batch must contain distinct ELF/input pairs")
        schedule = rounds(ids)
        if out.exists():
            result = manifest(out, inputs, schedule, placement, creating=False)
            require(gate.exact_json(read_json((out / "manifest.json").read_bytes()), result),
                    "batch manifest differs from the ordered inputs or round schedule")
        else:
            with tempfile.TemporaryDirectory(dir=out.parent) as tmp:
                draft = Path(tmp) / "batch"
                draft.mkdir(mode=0o700)
                for name, item in inputs.items():
                    packet = draft / name
                    packet.mkdir(mode=0o700, parents=True)
                    (packet / "policy-0.json").write_bytes(item.policy)
                for index, round_ids in enumerate(schedule, 1):
                    for name in round_ids:
                        item = inputs[name]
                        anchor.calibrate(item.elf, item.stdin, item.capture / "full", draft / name / f"policy-{index}.json")
                result = manifest(draft, inputs, schedule, placement, creating=True)
                require(gate.placement() == placement, "placement changed during batch calibration")
                anchor.save_new(draft / "manifest.json", result)
                draft.rename(out)
        require(gate.placement() == placement, "placement changed while reading batch")
        return result
    finally:
        os.close(descriptor)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--items", type=Path, required=True, help="ordered JSON array of id, elf, stdin, capture")
    cli.add_argument("--out", type=Path, required=True, help="batch directory; parent must already exist")
    args = cli.parse_args()
    try:
        items = read_json(args.items.read_bytes())
        for item in items:
            for key in ("elf", "stdin", "capture"):
                item[key] = args.items.parent / item[key]
        result = prepare(items, args.out)
    except (ValueError, OSError, TypeError, KeyError, IndexError, OverflowError) as error:
        cli.exit(1, f"{error}\n")
    print(json.dumps(result))
