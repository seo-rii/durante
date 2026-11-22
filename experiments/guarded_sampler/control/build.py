"""Build the static two-load-site ELF and reproduce its output and load-count oracles."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def cycle_sum(values, visits):
    cycles, tail = divmod(visits, len(values))
    return cycles * sum(values) + sum(values[:tail])


def build(out):
    out.mkdir()
    obj, elf = out / "path_mix.o", out / "path_mix"
    subprocess.run(["as", "--64", str(HERE / "path_mix.S"), "-o", obj], check=True)
    subprocess.run(["ld", "-static", "-no-pie", "--build-id=none", "-z", "noexecstack", "-Ttext-segment=0x400000",
                    "-o", elf, obj], check=True)
    symbols = {}
    for line in subprocess.check_output(["nm", "--defined-only", elf], text=True).splitlines():
        address, _, label = line.split()
        if label in ("forward_load", "reverse_load"):
            symbols[label] = int(address,16) - 0x400000
    binary = elf.read_bytes()
    with tarfile.open(out / "elf.tar.xz", "w:xz", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("bin/path_mix")
        member.mode, member.size = 0o755, len(binary)
        archive.addfile(member, io.BytesIO(binary))
    cases = []
    words = [17 * i + 3 for i in range(256)]
    reverse = [(v ^ 0x5a) + 7 for v in reversed(words)]
    for label, mode, first, second in (("train", "A", 7_920_000, 80_000), ("shift", "B", 400_000, 7_600_000)):
        checksum = cycle_sum(words, first) + cycle_sum(reverse, second)
        stdin, stdout = mode + "\n", f"{checksum:016x}\n"
        run = subprocess.run([elf.resolve()], input=stdin.encode(), capture_output=True, check=True)
        if run.stdout != stdout.encode() or run.stderr:
            raise ValueError("workload did not reproduce the independent checksum")
        sites = []
        for symbol, visits in (("forward_load", first), ("reverse_load", second)):
            periods, rem = divmod(visits, 1024)
            pairs = periods + (rem >= 2)
            sites.append({"offset": symbols[symbol], "loads": visits, "pairs": pairs, "near_pairs": pairs,
                          "far_pairs": 0, "sampled_address_span_bytes": 4})
        cases.append({"case_id": "path_mix/" + label, "program": "path_mix", "binary_sha256": sha(binary),
                      "split": "calibration" if mode == "A" else "holdout", "population": "control",
                      "stdin": stdin, "stdout": stdout, "stdin_sha256": sha(stdin.encode()),
                      "stdout_sha256": sha(stdout.encode()), "mix_oracle": sites})
    (out / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out", type=Path, help="new directory for the frozen executable, archive and case manifest")
    build(parser.parse_args().out)
