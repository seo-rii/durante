"""Assemble the guest archive from the byte-verified Aonohako scoped-judge payload."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import tarfile

HERE = Path(__file__).resolve().parent
PILOT = "314b263c93bc898fb0b91f2e40d2482e2a1a7de2"
LOCATION = "docs/durante/gce-qualified-judge"


def sha(path):
    with path.open("rb") as data:
        return hashlib.file_digest(data, "sha256").hexdigest()


def main():
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--aonohako", type=Path, required=True)
    cli.add_argument("--pilot-payload", type=Path, required=True)
    cli.add_argument("--output", type=Path, required=True, help="new payload directory")
    cli.add_argument("--registration", type=Path, required=True)
    args = cli.parse_args()
    raw = subprocess.check_output(["git", "show", f"{PILOT}:{LOCATION}/registration.json"], cwd=args.aonohako)
    pilot = json.loads(raw)
    root = args.output.resolve()
    root.mkdir()
    for name, digest in pilot["payload"].items():
        src = args.pilot_payload / name
        if sha(src) != digest:
            raise ValueError(f"pilot payload differs at {name}")
        dst = root / "pilot" / name
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
    for name in ("guest.sh", "guest.py", "case_manifest.json"):
        shutil.copyfile(HERE / name, root / name)
    payload = {str(p.relative_to(root)): sha(p) for p in sorted(root.rglob("*")) if p.is_file()}
    archive = root.with_suffix(".tar.gz")
    with archive.open("xb") as raw_out, gzip.GzipFile(fileobj=raw_out, mode="wb", filename="", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w|", format=tarfile.GNU_FORMAT) as output:
            for p in sorted(root.rglob("*")):
                info = output.gettarinfo(str(p), str(p.relative_to(root)))
                info.uid = info.gid = info.mtime = 0
                info.uname = info.gname = ""
                info.mode = 0o755 if p.is_dir() or p.name == "guest.sh" else 0o644
                if p.is_file():
                    with p.open("rb") as stream:
                        output.addfile(info, stream)
                else:
                    output.addfile(info)
    result = dict(schema=1, archive_sha256=sha(archive), pilot_commit=PILOT,
                  pilot_registration_sha256=hashlib.sha256(raw).hexdigest(), payload=payload)
    args.registration.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
