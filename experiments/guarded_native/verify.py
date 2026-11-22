"""Verify native wait4 measurements, ELF identities, outputs, pinned exec events and resource cleanup."""
import argparse
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from cpp20_locality.verify import read_compressed, require, sha, verify_collection, verify_elfs
from first_run_sampler.prepare import control_binary
from guarded_sampler.prepare import mix_binary
from guarded_native.prepare import GUARDED, registration, sources

RESULT = HERE.parent / "results/gce-guarded-native"


def verify(result, elf_archive=None):
    protocol = (HERE / "protocol.json").read_bytes()
    plan = registration()
    require(plan == json.loads(protocol), "different native protocol")
    files = plan["tools_sha256"]
    for name, origin in sources().items():
        require(sha(origin.read_bytes()) == files[name], "different source: " + name)
    cases = json.loads((GUARDED / "cases.json").read_bytes())
    if elf_archive:
        verify_elfs(elf_archive, [c for c in cases if c["population"] == "real"])
    require(sha(control_binary()) == files["bin/path_switch"] and sha(mix_binary()) == files["bin/path_mix"],
            "different control binary")
    for c in cases:
        require(all(sha(c[k].encode()) == c[k + "_sha256"] for k in ("stdin", "stdout")), "different case bytes")
    require(verify_collection(result, protocol, cases, set(), set()) == (180, 0, 0), "wrong native collection")
    return read_compressed(result / "dataset.json.gz")["platform"]["cpu_steal_ticks"]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("result", type=Path, nargs="?", default=RESULT)
    parser.add_argument("--elf-archive", type=Path, help="Aonohako #154 corpus/elfs.tar.xz")
    args = parser.parse_args()
    steal = verify(args.result, args.elf_archive)
    print(f"Verified 18 warmups and 162 native wait4 observations on CPU 1; {steal} steal ticks.")
