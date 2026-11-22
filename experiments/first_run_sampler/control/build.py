"""Build the frozen path-switch ELF and regenerate its input/output and load-site oracles."""
import argparse
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
ELF_SHA = "f507857b15e8b6c665087d93c87f962b221bf9a72808283bc66cb6737b8cbddf"
FORWARD_PC, REVERSE_PC = 0x1706, 0x173e


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("destination", type=Path, help="new directory for the executable, archive and cases")
    args = parser.parse_args()
    out = args.destination
    out.mkdir()
    elf = out / "path_switch"
    subprocess.run(["g++", "-std=c++20", "-O2", "-static", "-no-pie", "-march=x86-64-v2",
                    "-Wl,--build-id=none", str(HERE / "path_switch.cpp"), "-o", str(elf)], check=True)
    binary = elf.read_bytes()
    if sha(binary) != ELF_SHA:
        raise ValueError("wrong path_switch ELF; use the documented toolchain")
    with tarfile.open(out / "elf.tar.xz", "w:xz", format=tarfile.USTAR_FORMAT) as archive:
        member = tarfile.TarInfo("bin/path_switch")
        member.mode, member.size = 0o755, len(binary)
        archive.addfile(member, io.BytesIO(binary))

    cases = []
    for label, mode, steps in (("train", "A", 2_097_573), ("same", "A", 4_194_719), ("switch", "B", 4_194_719)):
        forward = mode == "A"
        values = [17 * i + 3 for i in range(256)]
        if not forward:
            values = [v ^ 0x5a for v in reversed(values)]
        blocks, remainder = divmod(steps, 256)
        stdin = f"{mode} {steps}\n"
        stdout = f"{blocks * sum(values) + sum(values[:remainder])}\n"
        result = subprocess.run([elf.resolve()], input=stdin.encode(), capture_output=True, check=True)
        if result.stdout != stdout.encode() or result.stderr:
            raise ValueError("control output differs from the independent sum")
        periods, extra = divmod(steps, 1024)
        pairs = periods + (extra >= 2)
        cases.append({"case_id": "path_switch/" + label, "program": "path_switch", "binary_sha256": ELF_SHA,
                      "stdin": stdin, "stdout": stdout, "stdin_sha256": sha(stdin.encode()),
                      "stdout_sha256": sha(stdout.encode()),
                      "oracle": {"offset": FORWARD_PC if forward else REVERSE_PC,
                                 "inactive_offset": REVERSE_PC if forward else FORWARD_PC,
                                 "visits": steps, "pairs": pairs, "near_pairs": pairs, "far_pairs": 0,
                                 "sampled_address_span_bytes": 4}})
    (out / "cases.json").write_text(json.dumps(cases, indent=2) + "\n")


if __name__ == "__main__":
    main()
