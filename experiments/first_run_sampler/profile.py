"""Derive one exact-ELF site profile from a trusted full-sampler calibration launch.

The full and profiled clients share the schema-2 JSON ABI. A trusted launcher
supplies the observed PID, arm, ELF identity and stdin. The reported relative
PCs alone cannot establish which executable or sampler produced them.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile

U64_MAX = 2**64 - 1
MAX_SITES = 16384
MAX_PROFILE = 64
MAX_RECEIPT_BYTES = 4 * 1024 * 1024
COUNTS = ("loads", "pairs", "near_pairs", "far_pairs")


def require(ok, message):
    if not ok:
        raise ValueError(message)


def uint(value):
    return type(value) is int and 0 <= value <= U64_MAX


def digest(value):
    return isinstance(value, str) and re.fullmatch("[0-9a-f]{64}", value) is not None


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read_json(raw):
    def unique(pairs):
        obj = dict(pairs)
        require(len(obj) == len(pairs), "duplicate JSON key")
        return obj
    return json.loads(raw, object_pairs_hook=unique)


def calibration_sites(raw, pid):
    require(0 < len(raw) <= MAX_RECEIPT_BYTES, "invalid calibration receipt length")
    receipt = read_json(raw)
    identity = {"schema": 2, "pid": pid, "threads": 1, "period": 1024, "line_bytes": 64}
    require(type(pid) is int and 0 < pid < 2**32, "invalid launcher PID")
    require(isinstance(receipt, dict) and set(receipt) == identity.keys() | set(COUNTS) | {"sites"},
            "wrong calibration receipt fields")
    require(all(uint(receipt[k]) and receipt[k] == v for k, v in identity.items()), "wrong full-sampler identity")
    sites = receipt["sites"]
    require(isinstance(sites, list) and 0 < len(sites) <= MAX_SITES, "no eligible loads or too many calibration sites")
    sums = dict.fromkeys(COUNTS, 0)
    previous = -1
    for site in sites:
        require(isinstance(site, dict) and set(site) == set(COUNTS) | {"offset", "sampled_address_span_bytes"} and
                all(uint(v) for v in site.values()), "invalid full-sampler row")
        offset, loads, pairs = (site[k] for k in ("offset", "loads", "pairs"))
        require(offset > previous and loads > 0, "unordered, repeated or unexecuted PC")
        previous = offset
        periods, extra = divmod(loads, 1024)
        require(pairs == periods + (extra >= 2) == site["near_pairs"] + site["far_pairs"] and
                (loads != 1 or site["sampled_address_span_bytes"] == 0), "invalid pair or span accounting")
        for key in COUNTS:
            sums[key] += site[key]
    require(all(uint(receipt[k]) and sums[k] == receipt[k] for k in COUNTS), "per-site sums disagree")
    return sites


def capture_for_digest(elf_sha256, stdin, receipt, pid, *, arm):
    """Use identities from one completed trusted launch; stdin and receipt are exact bytes."""
    require(digest(elf_sha256) and arm == "full", "capture requires the executed ELF digest and full-sampler arm")
    sites = calibration_sites(receipt, pid)
    total = sum(s["loads"] for s in sites)
    prefix = []
    covered = 0
    for site in sorted(sites, key=lambda s: (-s["loads"], s["offset"]))[:MAX_PROFILE]:
        prefix.append({"offset": site["offset"], "visits": site["loads"]})
        covered += site["loads"]
        if 100 * covered >= 95 * total:
            break
    return {"schema": 1, "elf_sha256": elf_sha256,
            "calibration": {"arm": arm, "pid": pid, "stdin_sha256": sha(stdin), "receipt_sha256": sha(receipt)},
            "offsets": sorted(s["offset"] for s in prefix), "ranked_prefix": prefix,
            "eligible_visits": total, "selected_visits": covered}


def resolve_for_digest(profile, elf_sha256):
    """Resolve an already-hashed executable to the decimal -sites argument."""
    fields = {"schema", "elf_sha256", "calibration", "offsets", "ranked_prefix", "eligible_visits", "selected_visits"}
    require(isinstance(profile, dict) and set(profile) == fields and uint(profile["schema"]) and
            profile["schema"] == 1 and digest(elf_sha256) and profile["elf_sha256"] == elf_sha256,
            "profile does not match this ELF or schema")
    source = profile["calibration"]
    require(isinstance(source, dict) and set(source) == {"arm", "pid", "stdin_sha256", "receipt_sha256"} and
            source["arm"] == "full" and type(source["pid"]) is int and 0 < source["pid"] < 2**32 and
            digest(source["stdin_sha256"]) and digest(source["receipt_sha256"]), "invalid calibration binding")
    prefix, offsets = profile["ranked_prefix"], profile["offsets"]
    require(isinstance(prefix, list) and 1 <= len(prefix) <= MAX_PROFILE and isinstance(offsets, list) and
            len(offsets) == len(prefix) and all(uint(n) for n in offsets), "invalid selected offset count")
    require(all(isinstance(row, dict) and set(row) == {"offset", "visits"} and
                uint(row["offset"]) and uint(row["visits"]) and row["visits"] > 0 for row in prefix), "invalid ranking")
    require(prefix == sorted(prefix, key=lambda s: (-s["visits"], s["offset"])) and
            offsets == sorted({row["offset"] for row in prefix}), "invalid ranking order or offset projection")
    total, covered = profile["eligible_visits"], profile["selected_visits"]
    require(uint(total) and uint(covered) and 0 < covered <= total and covered == sum(s["visits"] for s in prefix)
            and (len(prefix) == MAX_PROFILE or 100 * covered >= 95 * total) and
            100 * (covered - prefix[-1]["visits"]) < 95 * total, "invalid selection totals or stopping rule")
    return ",".join(map(str, offsets))


def elf_digest(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def capture(elf_path, stdin, receipt, pid, *, arm):
    return capture_for_digest(elf_digest(elf_path), stdin, receipt, pid, arm=arm)


def resolve(profile, elf_path):
    return resolve_for_digest(profile, elf_digest(elf_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("capture", help="create a new profile from a trusted calibration")
    create.add_argument("--elf", type=Path, required=True)
    create.add_argument("--stdin", type=Path, required=True)
    create.add_argument("--receipt", type=Path, required=True)
    create.add_argument("--pid", type=int, required=True)
    create.add_argument("--arm", choices=["full"], required=True)
    create.add_argument("--out", type=Path, required=True)
    use = commands.add_parser("resolve", help="print the registered -sites argument for this ELF")
    use.add_argument("--elf", type=Path, required=True)
    use.add_argument("--profile", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "capture":
        with args.receipt.open("rb") as stream:
            receipt = stream.read(MAX_RECEIPT_BYTES + 1)
        result = capture(args.elf, args.stdin.read_bytes(), receipt, args.pid, arm=args.arm)
        # Publish a complete file without replacing another launch's calibration.
        with tempfile.NamedTemporaryFile(dir=args.out.parent, mode="w", encoding="utf-8") as tmp:
            json.dump(result, tmp, indent=2)
            tmp.write("\n")
            tmp.flush()
            os.link(tmp.name, args.out)
    else:
        print(resolve(read_json(args.profile.read_bytes()), args.elf))


if __name__ == "__main__":
    main()
