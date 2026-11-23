"""Assemble a fresh guest directory from the registered guarded-sampler payload and static workloads."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


def digest(file):
    with file.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(out, runtime, *, study=HERE, probe=None, benchmark="bench.py", source_dirs=()):
    plan = json.loads((study / "plan.json").read_bytes())
    if (runtime / "protocol.json").read_bytes() != (HERE.parent / "guarded_sampler/protocol.json").read_bytes():
        raise ValueError("expected the registered guarded-sampler runtime")
    out.mkdir(mode=0o700)
    shutil.copytree(runtime, out / "runtime", symlinks=True,
                    ignore=shutil.ignore_patterns("__pycache__", "output", "*.pyc"))
    # The shared reader and modules retain their repository-relative imports on the guest.
    shutil.copytree(HERE.parent, out / "repo/experiments",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "results"))
    shutil.copyfile(runtime / "production_reader.py", out / "repo/collect.py")
    binaries = out / "binaries"
    binaries.mkdir()
    for program, expected in {c["program"]: c["binary_sha256"] for c in plan["cases"]}.items():
        src = next((path for directory in (study, *source_dirs, HERE)
                    if (path := directory / (program + ".cpp")).is_file()), None)
        if src is not None:
            subprocess.run(["g++", "-std=c++20", "-O2", "-static", "-s", "-march=x86-64-v2",
                            "-Wl,--build-id=none", src, "-o", binaries / program], check=True)
        else:
            shutil.copy2(runtime / "bin" / program, binaries / program)
        if digest(binaries / program) != expected:
            raise ValueError("different static workload: " + program)
    guest = (runtime / "guest.sh").read_text()
    command = '"$PWD/bench.py"'
    if guest.count(command) != 1:
        raise ValueError("different guest entry point")
    new_command = (f'"$PWD/repo/experiments/{study.name}/{benchmark}" '
                   '--payload "$PWD/runtime" --binaries "$PWD/binaries" --out "$PWD/output"')
    if probe is not None:
        expected = json.loads((study / "rules.json").read_bytes())["probe_sha256"]
        if digest(probe) != expected:
            raise ValueError("different registered temporal probe")
        shutil.copy2(probe, out / "temporal.so")
        new_command += ' --probe "$PWD/temporal.so"'
    (out / "guest.sh").write_text(guest.replace(command, new_command))
    (out / "guest.sh").chmod(0o700)
    files = sorted(p for p in out.rglob("*") if p.is_file())
    (out / "checksums.sha256").write_text("".join(f"{digest(p)}  {p.relative_to(out)}\n" for p in files))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path)
    parser.add_argument("--runtime", type=Path, required=True, help="verified #82 payload")
    args = parser.parse_args()
    prepare(args.out, args.runtime.resolve())
