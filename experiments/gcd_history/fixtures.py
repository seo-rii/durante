"""Reproduce six inputs, ELF identities, Euclidean histories and exact child outputs."""
import argparse
from collections import Counter
import hashlib
import io
import json
from math import gcd
from pathlib import Path
import runpy
import subprocess
import tarfile
from tempfile import TemporaryDirectory

HERE = Path(__file__).resolve().parent
EXP = HERE.parent
ROOT = EXP.parent
PLAN = HERE / "plan.json"
SOURCE = HERE / "permuted.rs"
DATA = EXP / "results/gce-gcd-history"
MANIFEST = DATA / "fixtures.json"
ARCHIVE = DATA / "permuted.tar.xz"
MEMBER = "bin/permuted/Main"
OLDER = EXP / "gcd_holdout/fixtures.py"
PREVIOUS = EXP / "results/gce-gcd-controls/holdout/fixtures.json"
prior = runpy.run_path(str(OLDER))
require, sha = prior["require"], prior["sha"]
BASES = ((34,21),(514229,317811))


class History:
    """Replay both pair hashes. Only the multiply-high table drives measured charges."""

    def __init__(self):
        self.last = self.penultimate = None
        self.one, self.two, self.low_hash = [None]*64, [None]*64, [None]*64
        self.changes = self.unpredicted = self.low_unpredicted = 0
        self.histogram = Counter()

    def visit(self, length):
        self.histogram[length] += 1
        last, earlier = self.last, self.penultimate
        if last is not None:
            one = last % 64
            predicted = low_predicted = self.one[one] == (last,length)
            self.one[one] = (last,length)
            if earlier is not None:
                mask = (1 << 64) - 1
                rotated = ((earlier << 32) & mask) | (earlier >> 32)
                two = (((rotated ^ last) * 0x9e3779b97f4a7c15) & mask) >> 58
                low = (31*earlier + last) % 64
                tag = (earlier,last,length)
                predicted |= self.two[two] == tag
                low_predicted |= self.low_hash[low] == tag
                self.two[two] = self.low_hash[low] = tag
            if length != last:
                self.changes += 1
                self.unpredicted += not predicted
                self.low_unpredicted += not low_predicted
        self.penultimate, self.last = last, length

    def summary(self, checksum):
        return {**prior["counts"](self.histogram,self.changes,checksum),
                "unpredicted_changes": self.unpredicted,
                "low_hash_unpredicted_changes": self.low_unpredicted}


def random_case(count, seed):
    words = runpy.run_path(str(prior["ORACLES"]))["random_words"](seed)
    history = History()
    checksum = 0
    for _ in range(count):
        a = max(1,next(words) & 0x7fffffff)
        b = max(1,next(words) & 0x7fffffff)
        history.visit(prior["trip_length"](a,b))
        checksum += gcd(a,b)
    return history.summary(checksum)


def permuted_positions(count, seed):
    # Invert each operation of the Rust permutation. Traverse canonical family
    # ranks and place each at its inverse image to independently reconstruct order.
    mask = (1 << 21) - 1
    require(count == mask + 1, "permutation requires 21 bits")
    inv1, inv2 = (pow(k, -1, count) for k in (0x45d9f3b,0x119de1f3))
    from array import array
    positions = array("I", [count])*count
    for pos in range(count):
        x = pos ^ (pos >> 11)
        x = (x * inv2) & mask
        x ^= (x >> 8) ^ (x >> 16)
        x = (x * inv1) & mask
        x ^= (x >> 10) ^ (x >> 20)
        i = (x-seed) & mask
        require(positions[i] == count, "permutation inverse collided")
        positions[i] = pos
    return positions


def fibonacci_case(count, parameter, program):
    if program == "permuted":
        positions = permuted_positions(count,parameter)
    else:
        width = 1 << parameter
        require(count % (2*width) == 0, "incomplete Fibonacci run pair")
        positions = (rank*2+family for base in range(0,count//2,width) for family in (0,1)
                     for rank in range(base,base+width))
    lengths = [prior["trip_length"](a,b) for a,b in BASES]
    history, families = History(), [Counter(),Counter()]
    ordered, checksum = hashlib.sha256(), 0
    for position in positions:
        rank, family = divmod(position,2)
        a,b = BASES[family]
        scale = 1 + (((rank*0x9e3779b9) % (1<<32)) >> 22)
        a, b = a*scale, b*scale
        require(max(a,b) < 2**31, "operand overflow")
        ordered.update(prior["PACK_PAIR"](a,b))
        families[family][scale] += 1
        checksum += gcd(a,b)
        # Scaling both positive inputs preserves every Euclidean quotient.
        history.visit(lengths[family])
    multiset = json.dumps([sorted(f.items()) for f in families],separators=(",",":")).encode()
    return {**history.summary(checksum),"family_scale_multiset_sha256":sha(multiset),
            "ordered_pairs_sha256":ordered.hexdigest()}


def build(out):
    subprocess.run(["rustc","--edition","2024","-O","--cfg","ONLINE_JUDGE","-C","strip=symbols",
                    "--remap-path-prefix",f"{ROOT}=.",str(SOURCE.relative_to(ROOT)),"-o",str(out)],
                   cwd=ROOT,check=True)


def publish_elf():
    with TemporaryDirectory() as tmp:
        path = Path(tmp)/"Main"
        build(path)
        raw = path.read_bytes()
    with tarfile.open(ARCHIVE,"x:xz") as tar:
        obj = tarfile.TarInfo(MEMBER)
        obj.mode, obj.size = 0o755, len(raw)
        tar.addfile(obj,io.BytesIO(raw))
    return subprocess.check_output(["rustc","--version","--verbose"],text=True)


def fixtures(publish, materialize, run, rebuild):
    old = {c["workload_id"]:c for c in json.loads(PREVIOUS.read_bytes())["cases"]}
    compiler = publish_elf() if publish else json.loads(MANIFEST.read_bytes())["permuted_compiler"]
    refs = {prog:(old[key]["binary_archive"],old[key]["archive_member"]) for prog,key in
            (("gcd","gcd_seed_b"),("fibonacci","fibonacci_alternating"))}
    refs["permuted"] = (str(ARCHIVE.relative_to(ROOT)),MEMBER)
    elfs = {prog:prior["member"](ROOT/path,member) for prog,(path,member) in refs.items()}
    if rebuild:
        with TemporaryDirectory() as tmp:
            out = Path(tmp)/"Main"
            build(out)
            require(out.read_bytes()==elfs["permuted"],"new compiler build differs from archived ELF")
    cases = []
    for spec in json.loads(PLAN.read_bytes())["cases"]:
        name,program,split = (spec[k] for k in ("workload_id","program","split"))
        inp = spec["stdin"].encode()
        count, param = map(int,inp.split())
        oracle = random_case(count,param) if program=="gcd" else fibonacci_case(count,param,program)
        stdout = f"{oracle['checksum']}\n".encode()
        archive,member = refs[program]
        binary = elfs[program]
        case = {"workload_id":name,"split":split,"binary_sha256":sha(binary),"binary_archive":archive,
                "archive_member":member,"input_file":name+".in","input_size":len(inp),"input_sha256":sha(inp),
                "stdin_hex":inp.hex(),"expected_sha256":sha(stdout),"stdout_hex":stdout.hex(),"oracle":oracle}
        if split=="anchor":
            ref = old[name]
            require(all(case[k]==v for k,v in ref.items() if k!="oracle") and
                    all(oracle[k]==v for k,v in ref["oracle"].items()),"anchor differs from previous study")
        if name.startswith("fibonacci"):
            previous = old["fibonacci_alternating"]["oracle"]
            require(all(oracle[k]==previous[k] for k in ["length_histogram","checksum","pairs","divisions",
                    "family_scale_multiset_sha256"]),"Fibonacci operand multisets differ")
        if materialize:
            path = materialize/"elfs"/name/"Main"
            prior["materialize"](path,binary,0o700)
            prior["materialize"](materialize/"inputs"/case["input_file"],inp,0o600)
            if run:
                child = subprocess.run([path],input=inp,capture_output=True,check=True,timeout=60)
                require(child.stdout==stdout and not child.stderr,"child checksum differs")
        cases.append(case)
    sources = [PLAN, SOURCE, OLDER, PREVIOUS, prior["ORACLES"], prior["OLD_SOURCE"], prior["SOURCE"]]
    value = {"schema":1,"cases":cases,"permuted_compiler":compiler,"permuted_archive_sha256":sha(ARCHIVE.read_bytes()),
             "source_sha256":{str(k.relative_to(ROOT)):sha(k.read_bytes()) for k in sources}}
    if publish:
        with MANIFEST.open("x") as f:
            f.write(json.dumps(value,indent=2)+"\n")
    else:
        require(json.loads(MANIFEST.read_bytes())==value,"fixture reconstruction differs")
    print("Verified both anchors, four prospective inputs and all 64-slot successor oracle counts.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--publish",action="store_true",help="create the frozen archive and fixture manifest once")
    parser.add_argument("--materialize",type=Path,help="root of generated inputs/ and elfs/")
    parser.add_argument("--run",action="store_true",help="check exact output from each materialized binary")
    parser.add_argument("--rebuild",action="store_true",help="require byte-identical compiler output")
    args = parser.parse_args()
    if args.run and not args.materialize:
        parser.error("--run needs --materialize")
    fixtures(args.publish,args.materialize,args.run,args.rebuild)
