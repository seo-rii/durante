"""Collect the optional production operand-byte counter on the registered #114 Dijkstra executables."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
MILAN = HERE.parent


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def schedule(names):
    for rep in (1, 2, 3):
        for i, name in enumerate(names if rep != 2 else reversed(names)):
            modes = ("base", "operand")
            for mode in modes if (i + rep) % 2 else modes[::-1]:
                yield rep, name, mode


def collect(root, cpu):
    plan = read(MILAN / "frozen.json")
    reg = {r["path"]:r["sha256"] for r in read(MILAN / "registration.json")["files"]}
    names = plan["protocol"]["groups"]["dijkstra"]
    files = ["plan.json","probe.so","dynamorio/bin64/drrun","dynamorio/lib64/release/libdynamorio.so"]
    files += [f"bin/{n}" for n in names] + [plan["cases"][n]["stdin"] for n in names]
    identities = {n: digest((root / n).read_bytes()) for n in files}
    if any(v != reg[n] for n,v in identities.items()):
        raise ValueError("input, executable, diagnostic client or runtime differs from the registered guest")
    os.sched_setaffinity(0, {cpu})
    output = []
    for rep, name, mode in schedule(names):
        with tempfile.TemporaryDirectory() as tmp:
            slot = Path(tmp)
            spec = plan["cases"][name]
            cmd = [root/"dynamorio/bin64/drrun", "-root", root/"dynamorio", "-quiet", "-disable_traces",
                   "-c", root/"probe.so", "-outdir", slot, "-rep_bytes", "-latency_bound", "-cpuid_profile",
                   plan["protocol"]["cpuid"]]
            if mode == "operand":
                cmd.append("-memory_operand_bytes")
            with (root / spec["stdin"]).open("rb") as inp, (slot/"out").open("wb") as out, (slot/"err").open("wb") as err:
                with subprocess.Popen([*cmd, "--", root / "bin" / name], cwd=root, stdin=inp, stdout=out, stderr=err,
                                      env={"PATH":os.defpath,"LC_ALL":"C","DYNAMORIO_CONFIGDIR":tmp}) as child:
                    _, status, usage = os.wait4(child.pid, 0)
                    child.returncode = os.waitstatus_to_exitcode(status)
            logs = list(slot.glob("process-*.jsonl"))
            stdout = (slot/"out").read_bytes()
            if child.returncode or len(logs) != 1 or logs[0].name != f"process-{child.pid}.jsonl" or (
                    digest(stdout) != spec["expected_sha256"] or (slot/"err").stat().st_size):
                raise ValueError("failed to complete the registered program with its original output")
            output.append(dict(rep=rep, name=name, mode=mode, pid=child.pid, exit_code=child.returncode,
                               cpu_us=round(usage.ru_utime*1e6)+round(usage.ru_stime*1e6), stdout=stdout.decode(),
                               records=logs[0].read_text()))
    cpu_model, = {line.split(":",1)[1].strip() for line in Path("/proc/cpuinfo").read_text().splitlines()
                  if line.startswith("model name")}
    return dict(schema=1, cpu=cpu, cpu_model=cpu_model, kernel=platform.release(), files=identities, trials=output)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", required=True, type=Path)
    cli.add_argument("--cpu", required=True, type=int)
    cli.add_argument("--out", required=True, type=Path)
    opts = cli.parse_args()
    data = collect(opts.payload.resolve(), opts.cpu)
    with opts.out.open("xb") as out:
        out.write(gzip.compress((json.dumps(data,indent=2)+"\n").encode(),mtime=0))
