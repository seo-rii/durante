"""Compare the actual compiler-specialized client with the full probe on two fixed controls."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def observe(client, drrun, program, root):
    lib = root / "probe.so"
    shutil.copyfile(client, lib)
    out = root / "records"
    out.mkdir()
    command = [drrun, "-quiet", "-disable_traces", "-c", lib, "-outdir", out, "-rep_bytes", "-latency_bound",
               "-cpuid_profile", "x86-64-v2", "-probe_l1d", "-probe_outer", "--", program]
    p = subprocess.run(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       env={"LC_ALL":"C", "PATH":os.defpath, "DYNAMORIO_CONFIGDIR":str(root)}, check=True, timeout=60)
    path, = out.glob("process-*.jsonl")
    end, = (r for r in map(json.loads, path.read_bytes().splitlines()) if r["event"] == "end")
    assert end["valid"] and end["quality"] == "uninterrupted"
    labels = [dict(re.findall(r"(\w+)=([0-9,]+)", s)) for s in p.stderr.decode().splitlines()]
    if len(labels) == 2:
        l1, o = labels
        entries = (int(o["l1d_dependent"]), int(o["dependent"].split(",")[1]), int(o["stream"].split(",")[1]))
        skipped = int(l1["skipped"])
    else:
        o, = labels
        assert o["capacity_lines"] == "32768"
        entries = tuple(int(o[k]) for k in ("l1d_dependent", "dependent_2m", "stream_2m"))
        skipped = int(o["skipped"])
    shutil.rmtree(out)
    return (end["latency_bound_x16"], end["rep_movs_bytes"] + end["rep_stos_bytes"], *entries), skipped, p.stdout


parser = argparse.ArgumentParser(description=__doc__)
for name in ("full", "reduced", "drrun", "control", "dependency"):
    parser.add_argument("--" + name, type=Path, required=True)
args = parser.parse_args()
with tempfile.TemporaryDirectory() as tmp:
    for program in (args.control, args.dependency):
        full = observe(args.full, args.drrun, program, Path(tmp))
        reduced = observe(args.reduced, args.drrun, program, Path(tmp))
        assert full == reduced, (program, full, reduced)
        if program == args.dependency:
            assert full[0][2:4] == (2, 2)
        else:
            assert full[0][1] == 72
