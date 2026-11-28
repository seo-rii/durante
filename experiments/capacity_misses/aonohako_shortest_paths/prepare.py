"""Reassemble the pinned graph input, original contestants, models and corrected probe."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile

HERE = Path(__file__).resolve().parent
MILAN = HERE.parent / "milan"
AONO_COMMIT = "ff46df539edaedef38401dc548d61e10a022e487"
PANEL = "14ffbd39e58719d859260d1d38e8531d0f219471dcff4f53e2bcd390f8e14eb6"
LIVE = "6b5f0fd0acc8f0e1007961c3accd53593069fb0919cb8b0fdbb0b64c35b0f5f8"
TRANSFER = "a859f51ea83b5e417161b92755d06235b2841d6ded267203517f35d98fb2071b"
POLICY = "65239f86426584c275a44b15038324dfaeeeb34ce8ebaebda2d43275aeddd862"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def checked(path, digest):
    data = path.read_bytes()
    if sha(data) != digest:
        raise ValueError("different file: " + str(path))
    return data


def parent_files():
    parent = json.loads((MILAN / "registration.json").read_bytes())
    hashes = {item["path"]: item["sha256"] for item in parent["files"]}
    original = dict(payload=hashes, archive_sha256=parent["archive_sha256"], expected_rows=parent["expected_rows"])
    if (len(hashes) != len(parent["files"]) or parent["freeze_commit"] != "eb28afa52558cd2772cf79a88e2136b812a94421"
            or sha((json.dumps(original, indent=2) + "\n").encode()) != parent["original_registration_sha256"]
            or parent["original_registration_sha256"] != "2f97caa72e03bd53da15ae8bf71216bf34c3820ababb9fdd57a997544a997c5d"):
        raise ValueError("different registered Milan probe payload")
    return hashes


def prepare(args):
    old = parent_files()
    frozen = json.loads((MILAN / "frozen.json").read_bytes())
    protocol = json.loads((HERE / "protocol.json").read_bytes())
    study = args.aonohako / "docs/durante"
    boundary = study / "gce-shortest-paths-boundary"
    registration = json.loads(checked(boundary / "registration.json", PANEL))
    prior = json.loads(checked(boundary / "results.json", LIVE))["engines"]["v13"]
    transfer = json.loads(checked(study / "gce-v13-judge-transfer/registration.json", TRANSFER))
    scope = registration["plans"]["v13"]
    policy = json.loads(checked(study / "gce-qualified-judge/refit/freeze.json", POLICY))["candidate"]["policy"]
    source = scope["cases"]["fast"]
    stdin, oracle = source["stdin"].encode(), source["expected_stdout"].encode()
    dest = args.output
    dest.mkdir()
    for member, digest in old.items():
        if member == "plan.json" or member.startswith(("inputs/", "bin/")):
            continue
        path = args.corrected / member
        checked(path, digest)
        (dest / member).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest / member)
    (dest / "bin").mkdir()
    (dest / "inputs").mkdir()
    (dest / "inputs/graph").write_bytes(stdin)
    cases = {}
    for name in protocol["programs"]:
        if name == "reference":
            exe = args.pilot_corpus / "shortest_paths-reference"
            digest = transfer["payload"]["pilot/corpus/shortest_paths-reference"]
        else:
            c = scope["cases"][name]
            exe = args.boundary_payload / c["binary"]
            digest = c["binary_sha256"]
            if c["stdin"].encode() != stdin or c["solver_stdout"].encode() != oracle:
                raise ValueError("different input or oracle")
        checked(exe, digest)
        target = dest / "bin" / name
        shutil.copyfile(exe, target)
        target.chmod(0o755)
        cases[name] = dict(stdin="inputs/graph", stdin_sha256=sha(stdin), expected_sha256=sha(oracle), elf_sha256=digest)
    measured = {p: {name: {k: panel[name][k] for k in ("native", "clock", "clock_termination")}
                    for name in protocol["programs"] if name != "reference"} for p, panel in prior["panels"].items()}
    plan = dict(protocol=protocol, cases=cases, input=stdin.decode(), expected_stdout=oracle.decode(),
                aonohako_revision=AONO_COMMIT, existing_live=measured, original_binding=scope["baseline_binding"],
                original_clock={k: policy[k] for k in ("instruction_ns_numerator", "rep_byte_ns_numerator", "ns_denominator")},
                ml4=frozen["weights"]["dijkstra"]["ML4"], corrected_probe_sha256=old["probe.so"])
    (dest / "plan.json").write_text(json.dumps(plan, indent=2) + "\n")



def package(payload, archive, registration):
    """Write deterministic archive members and the manifest checked on each VM."""
    hashes = {}
    with archive.open("xb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for path in sorted(p for p in payload.rglob("*") if p.is_file()):
                key = path.relative_to(payload).as_posix()
                hashes[key] = sha(path.read_bytes())
                info = tarfile.TarInfo(key)
                info.size = path.stat().st_size
                info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                with path.open("rb") as stream:
                    tar.addfile(info, stream)
    plan = json.loads((payload / "plan.json").read_bytes())
    rows = len(plan["cases"]) * len(plan["protocol"]["rounds"])
    registration.write_text(json.dumps(dict(payload=hashes, archive_sha256=sha(archive.read_bytes()),
                                            expected_rows={".":rows}), indent=2) + "\n")


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    for key in ("aonohako", "corrected", "boundary-payload", "pilot-corpus", "output"):
        cli.add_argument("--" + key, type=Path, required=True)
    args = cli.parse_args()
    prepare(args)
    package(args.output, args.output.with_suffix(".tar.gz"), args.output.with_suffix(".registration.json"))
