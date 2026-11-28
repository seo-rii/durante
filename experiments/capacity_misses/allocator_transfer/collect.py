"""Measure allocator entry traffic on previously registered static contest executables."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
CAPACITY = HERE.parent
STUDIES = ("milan", "order_statistics_transfer", "range_distinct", "dijkstra_relaxation")
NAMES = ("_Znwm", "__libc_malloc", "__libc_free", "__libc_calloc", "__libc_realloc")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read(path):
    return json.loads(path.read_bytes())


def require(ok, message):
    if not ok:
        raise ValueError(message)


def addresses(study, name, binary, scratch, compiler, flags):
    nm = subprocess.run(["nm", "-n", binary], check=True, capture_output=True, text=True)
    found = {parts[2]: parts[0] for line in nm.stdout.splitlines()
             if len(parts := line.split()) == 3 and parts[2] in NAMES}
    if found:
        require(set(found) == set(NAMES), "incomplete symbol table")
        return found
    group, solver = name.split("-")
    if study == "range_distinct":
        source = CAPACITY / study / "queries.cpp"
    elif study == "order_statistics_transfer":
        source = (CAPACITY / "predecessor/solve.cpp" if group in ("uniform", "clustered")
                  else CAPACITY / study / "inversions.cpp")
    else:
        raise ValueError("unexpected stripped executable: " + name)
    linkmap = scratch / "addresses.map"
    rebuilt = scratch / "rebuilt"
    subprocess.run([compiler, *flags, "-Wl,-Map," + str(linkmap), "-DSOLVER_" + solver.upper(),
                    source, "-o", rebuilt], check=True)
    require(digest(rebuilt) == digest(binary), "link-map build did not reproduce registered ELF")
    pattern = r"^\s*0x([0-9a-f]+)\s+(__libc_(?:malloc|free|calloc|realloc)|operator new\(unsigned long\))$"
    found = {("_Znwm" if symbol.startswith("operator new") else symbol): address
             for address, symbol in re.findall(pattern, linkmap.read_text(), re.MULTILINE)}
    require(set(found) == set(NAMES), "incomplete linker map")
    return found


def collect(payloads, runtime, client, cpu, compiler, scratch):
    os.sched_setaffinity(0, {cpu})
    launcher = runtime / "bin64/drrun"
    manifest = read(CAPACITY / "milan/registration.json")
    required = {f["path"]: f["sha256"] for f in manifest["files"]}
    hashes = {path: digest(runtime / path) for path in ("bin64/drrun", "lib64/release/libdynamorio.so")}
    require(all(value == required["dynamorio/" + key] for key, value in hashes.items()), "changed runtime")
    flags = read(CAPACITY / "order_statistics_transfer/frozen.json")["flags"]
    symbols, scheduled, programs = {}, [], {}
    for study, payload in zip(STUDIES, payloads, strict=True):
        plan = read(CAPACITY / study / "frozen.json")
        require(digest(payload / "plan.json") == digest(CAPACITY / study / "frozen.json"), "changed plan")
        for name, case in plan["cases"].items():
            elf = payload / "bin" / name
            stdin = payload / case["stdin"]
            elf_sha = digest(elf)
            require(elf_sha == case["elf_sha256"] and digest(stdin) == case["stdin_sha256"], "changed input")
            if elf_sha not in symbols:
                symbols[elf_sha] = addresses(study, name, elf, scratch, compiler, flags)
            scheduled.append((study, name))
            programs[study, name] = elf, stdin, case
    rows = []
    for repetition, order in enumerate((scheduled, list(reversed(scheduled))), 1):
        for study, name in order:
            elf, stdin, case = programs[study, name]
            # The supplied payload may be on a noexec mount. Use a fresh copy
            # at a fixed executable path for all trials and check every copy.
            target = scratch / "target"
            shutil.copyfile(elf, target)
            target.chmod(0o700)
            elf_sha = digest(target)
            require(elf_sha == case["elf_sha256"], "binary changed while staging")
            addr = symbols[elf_sha]
            cmd = [launcher, "-root", runtime, "-quiet", "-disable_traces", "-c", client,
                   *(addr[k] for k in NAMES), "--", target, *case.get("args", [])]
            with stdin.open("rb") as f:
                result = subprocess.run(cmd, stdin=f, capture_output=True, timeout=120, cwd=scratch,
                                        env={"PATH": "/usr/bin:/bin", "LANG": "C", "LC_ALL": "C"})
            require(result.returncode == 0 and hashlib.sha256(result.stdout).hexdigest() == case["expected_sha256"],
                    f"execution or output changed for {study}/{name}")
            rows.append(dict(study=study, case=name, repetition=repetition, elf_sha256=elf_sha,
                             stdin_sha256=case["stdin_sha256"], stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
                             stdout_bytes=len(result.stdout),
                             exit_code=result.returncode, stderr=result.stderr.decode()))
    return dict(runtime_sha256=hashes, client_sha256=digest(client), client_source_sha256=digest(HERE/"entries.c"),
                cpu=cpu, platform=platform.uname().machine, cpu_profile="local host",
                compiler=subprocess.check_output([compiler, "-dumpfullversion"], text=True).strip(),
                symbols=symbols, rows=rows)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for study in STUDIES:
        cli.add_argument("--" + study.replace("_", "-"), type=Path, required=True)
    cli.add_argument("--runtime", type=Path, required=True)
    cli.add_argument("--client", type=Path, required=True)
    cli.add_argument("--cpu", type=int, required=True)
    cli.add_argument("--compiler", default="g++")
    cli.add_argument("--output", type=Path, required=True)
    args = cli.parse_args()
    with tempfile.TemporaryDirectory() as tmp:
        data = collect([getattr(args, k).resolve() for k in STUDIES], args.runtime.resolve(),
                       args.client.resolve(), args.cpu, args.compiler, Path(tmp))
    args.output.write_bytes(gzip.compress(json.dumps(data, indent=2).encode(), mtime=0))
