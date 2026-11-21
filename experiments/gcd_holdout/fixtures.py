"""Freeze or reproduce five Rust GCD inputs, expected outputs and independent counts."""
import argparse
from collections import Counter
import hashlib
import io
import json
from math import gcd
from pathlib import Path
import runpy
import struct
import subprocess
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
EXP = ROOT / "experiments"
DATA = EXP / "results/gce-gcd-controls/holdout"
PLAN = HERE / "plan.json"
ARCHIVE = DATA / "fibonacci.tar.xz"
MANIFEST = DATA / "fixtures.json"
SOURCE = HERE / "fibonacci.rs"
OLD_SOURCE = EXP / "rust_instruction_mix/holdout/gcd.rs"
ORACLES = OLD_SOURCE.with_name("oracles.py")
ORIGINAL = EXP / "gcd_controls/protocol.json"
MEMBER = "bin/fibonacci/Main"
PACK_PAIR = struct.Struct("<II").pack


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def trip_length(a, b):
    length = 0
    while b:
        a, b = b, a % b
        length += 1
    return length


def counts(histogram, changes, checksum):
    return {"pairs": sum(histogram.values()), "divisions": sum(k*v for k,v in histogram.items()),
            "changes": changes, "checksum": checksum,
            "length_histogram": {str(k):v for k,v in sorted(histogram.items())}}


def random_case(count, seed):
    words = runpy.run_path(str(ORACLES))["random_words"](seed)
    histogram = Counter()
    previous = changes = checksum = 0
    for i in range(count):
        a = max(1, next(words) & 0x7fffffff)
        b = max(1, next(words) & 0x7fffffff)
        length = trip_length(a, b)
        changes += i > 0 and previous != length
        previous = length
        histogram[length] += 1
        checksum += gcd(a, b)
    return counts(histogram, changes, checksum)


def fibonacci_case(count, shift):
    # Enumerate runs explicitly. The program instead selects its family from bit
    # 'shift' of the global index, then removes that bit to obtain the family rank.
    width = 1 << shift
    require(count > 0 and count % (width * 2) == 0, "incomplete pair of runs")
    bases = [(34,21), (514229,317811)]
    lengths = [trip_length(a,b) for a,b in bases]
    require(len(set(lengths)) == 2 and all(gcd(a,b) == 1 for a,b in bases), "unexpected family")
    families = [Counter(), Counter()]
    input_hash = hashlib.sha256()
    changes = checksum = 0
    previous = None
    for base in range(0, count // 2, width):
        for family, (a,b) in enumerate(bases):
            changes += previous is not None and previous != lengths[family]
            previous = lengths[family]
            for rank in range(base, base + width):
                scale = 1 + (((rank * 0x9e3779b9) & 0xffffffff) >> 22)
                scaled_a, scaled_b = a * scale, b * scale
                # A positive common scale preserves every Euclidean quotient and trip count.
                require(scaled_a < 2**31 and scaled_b < 2**31, "operand overflow")
                families[family][scale] += 1
                checksum += gcd(scaled_a,scaled_b)
                input_hash.update(PACK_PAIR(scaled_a,scaled_b))
    multiset = json.dumps([sorted(freq.items()) for freq in families],separators=(",",":")).encode()
    return {**counts(dict.fromkeys(lengths,count//2),changes,checksum),
            "ordered_pairs_sha256": input_hash.hexdigest(), "family_scale_multiset_sha256": sha(multiset)}


def build(path):
    args = ["rustc","--edition","2024","-O","--cfg","ONLINE_JUDGE","-C","strip=symbols",
            "--remap-path-prefix",f"{ROOT}=.",str(SOURCE.relative_to(ROOT)),"-o",str(path)]
    subprocess.run(args, cwd=ROOT, check=True)


def member(path, name):
    with tarfile.open(path, "r:xz") as tar:
        entry = tar.getmember(name)
        require(entry.isfile() and 0 < entry.size < 16*1024*1024, "invalid ELF archive member")
        return tar.extractfile(entry).read()


def publish_elf():
    with TemporaryDirectory() as folder:
        out = Path(folder)/"Main"
        build(out)
        raw = out.read_bytes()
    with tarfile.open(ARCHIVE, "x:xz") as tar:
        entry = tarfile.TarInfo(MEMBER)
        entry.size, entry.mode = len(raw), 0o755
        tar.addfile(entry, io.BytesIO(raw))
    return subprocess.check_output(["rustc","--version","--verbose"],text=True)


def materialize(path, raw, mode):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        require(path.read_bytes() == raw, f"conflicting fixture at {path}")
    else:
        with path.open("xb") as f:
            f.write(raw)
        path.chmod(mode)


def fixtures(publish, output, run, rebuild):
    plan = json.loads(PLAN.read_bytes())
    original = next(c for c in json.loads(ORIGINAL.read_bytes())["cases"] if c["workload_id"] == "gcd")
    compiler = publish_elf() if publish else json.loads(MANIFEST.read_bytes())["fibonacci_compiler"]
    fib, old = member(ARCHIVE,MEMBER), member(ROOT/original["binary_archive"],original["archive_member"])
    require(sha(old)==original["binary_sha256"], "original GCD executable changed")
    if rebuild:
        with TemporaryDirectory() as folder:
            out = Path(folder)/"Main"
            build(out)
            require(out.read_bytes()==fib, "compiler output differs from archived Fibonacci ELF")
    programs = {"gcd": (old,original["binary_archive"],original["archive_member"]),
                "fibonacci": (fib,str(ARCHIVE.relative_to(ROOT)),MEMBER)}
    sources = [PLAN,SOURCE,OLD_SOURCE,ORACLES,ORIGINAL]
    value = {"schema":1, "source_sha256":{str(k.relative_to(ROOT)):sha(k.read_bytes()) for k in sources},
             "fibonacci_compiler":compiler, "fibonacci_archive_sha256":sha(ARCHIVE.read_bytes()), "cases":[]}
    fib_oracles = []
    for spec in plan["cases"]:
        name, prog = spec["workload_id"], spec["program"]
        stdin = spec["stdin"].encode()
        count, param = map(int,stdin.split())
        if prog == "gcd":
            oracle = random_case(count,param)
        else:
            oracle = fibonacci_case(count,param)
            fib_oracles.append(oracle)
        binary, archive, archive_member = programs[prog]
        stdout = f"{oracle['checksum']}\n".encode()
        input_file = name+".in"
        case = {"workload_id":name,"binary_archive":archive,"archive_member":archive_member,
                "binary_sha256":sha(binary),"input_file":input_file,"input_size":len(stdin),
                "stdin_hex":stdin.hex(),"input_sha256":sha(stdin),"stdout_hex":stdout.hex(),
                "expected_sha256":sha(stdout),"oracle":oracle}
        value["cases"].append(case)
        if output:
            elf = output/"elfs"/name/"Main"
            materialize(output/"inputs"/input_file,stdin,0o600)
            materialize(elf,binary,0o700)
            if run:
                child = subprocess.run([elf],input=stdin,capture_output=True,check=True,timeout=60)
                require(child.stdout==stdout and not child.stderr, f"unexpected output for {name}")
    first, second = fib_oracles
    require(all(first[k]==second[k] for k in ("pairs","divisions","checksum","length_histogram","family_scale_multiset_sha256"))
            and first["changes"]!=second["changes"] and first["ordered_pairs_sha256"]!=second["ordered_pairs_sha256"],
            "Fibonacci conditions are not a matched reordering")
    if publish:
        with MANIFEST.open("x") as f:
            f.write(json.dumps(value, indent=2)+"\n")
    else:
        require(json.loads(MANIFEST.read_bytes())==value,"fixture replay differs from manifest")
    print("Verified five Rust cases and the shared Fibonacci operand multiset.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish",action="store_true",help="create the initial ELF archive and manifest")
    parser.add_argument("--materialize",type=Path,help="root for generated inputs/ and elfs/")
    parser.add_argument("--run",action="store_true",help="validate child stdout on all materialized cases")
    parser.add_argument("--rebuild",action="store_true",help="require a byte-identical compiler rebuild")
    args = parser.parse_args()
    if args.run and not args.materialize:
        parser.error("--run requires --materialize")
    fixtures(args.publish,args.materialize,args.run,args.rebuild)
