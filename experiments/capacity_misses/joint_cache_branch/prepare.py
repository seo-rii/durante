"""Repackage the registered matcher payload with a new schedule and joint collector."""
import argparse
import gzip
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile

HERE = Path(__file__).resolve().parent
SOURCE = HERE.parent / "string_matching"


def encoded(value):
    return (json.dumps(value, indent=2) + "\n").encode()


def prepare(source, output, registration_file, plan_file):
    old = json.loads((SOURCE / "registration.json").read_bytes())
    if sha256(source.read_bytes()).hexdigest() != old["archive_sha256"]:
        raise ValueError("expected the registered string matcher archive")
    previous = json.loads((SOURCE / "frozen.json").read_bytes())
    plan = {key: previous[key] for key in ("cases", "clients", "ml4", "gshare_miss_ns")}
    plan["protocol"] = json.loads((HERE / "protocol.json").read_bytes())
    replacements = {"plan.json": encoded(plan), "guest.py": (HERE / "guest.py").read_bytes()}
    payload = {}
    with tarfile.open(source, "r:gz") as inp, output.open("xb") as dest:
        with gzip.GzipFile(filename="", mode="wb", fileobj=dest, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode="w|") as out:
            for member in inp:
                if not member.isfile() or member.name in payload:
                    raise ValueError("unexpected archive member")
                data = inp.extractfile(member).read()
                if sha256(data).hexdigest() != old["payload"][member.name]:
                    raise ValueError("different registered member " + member.name)
                data = replacements.get(member.name, data)
                member.size = len(data)
                out.addfile(member, io.BytesIO(data))
                payload[member.name] = sha256(data).hexdigest()
    if set(payload) != set(old["payload"]):
        raise ValueError("incomplete source archive")
    rows = len(plan["cases"]) * len(plan["protocol"]["rounds"])
    registration = dict(payload=payload, archive_sha256=sha256(output.read_bytes()).hexdigest(), expected_rows={".": rows})
    registration_file.write_bytes(encoded(registration))
    plan_file.write_bytes(encoded(plan))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("registration", type=Path)
    parser.add_argument("plan", type=Path)
    args = parser.parse_args()
    prepare(args.source, args.archive, args.registration, args.plan)
