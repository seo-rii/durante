"""Reuse verified preflight evidence for a trusted, input-deterministic static ELF; enforce each deadline afresh."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import secrets
import shutil
import stat
import sys
import tempfile

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from first_run_sampler.profile import digest, elf_digest, read_json, require, sha
from guarded_native import anchor, live
from shape_calibration import combined, run, select_policy, temporal


def object_hash(value):
    return sha(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


# Include the transitive Python readers and rules used for this preflight, including
# the study helpers loaded through runpy. The runtime manifest pins the released reader.
SOURCE_FILES = (
    "preflight_cache/session.py", "shape_calibration/combined.py", "shape_calibration/run.py",
    "shape_calibration/select_policy.py", "shape_calibration/shape.py", "shape_calibration/temporal.py",
    "footprint_admission/admission.py", "guarded_native/anchor.py", "guarded_native/live.py",
    "guarded_native/transfer.py", "guarded_native/evidence.py", "guarded_native/score.py",
    "guarded_sampler/guard.py", "guarded_sampler/cases.py", "first_run_sampler/profile.py",
    "cpp20_locality/score.py", "cpp20_locality/verify.py", "cpp20_locality/export.py",
    "cpp20_locality/fixtures.py", "cpp20_locality/schedule.py", "access_sample/study.py",
    "dependency_pairs/study.py",
)


def implementation_hash(experiments=HERE.parent):
    return object_hash({name: elf_digest(experiments / name) for name in SOURCE_FILES})


def session_context(cache, elf, method):
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = cache.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == os.geteuid() and info.st_mode & 0o077 == 0,
            "cache must be a private directory owned by this user")
    marker = cache / "session.json"
    if not marker.exists():
        try:
            anchor.save_new(marker, {"id": secrets.token_hex(32)})
        except FileExistsError:
            pass  # A concurrent first caller published the session ID.
    session_id = read_json(marker.read_bytes())["id"]
    require(digest(session_id), "invalid cache session")
    cpus = sorted(os.sched_getaffinity(0))
    require(len(cpus) == 1, "pin the caller to one CPU before preparing a calibration")
    require(method in select_policy.METHODS, "unknown selection method")
    invocation = object_hash([str(elf), os.getcwd(), dict(os.environ)])
    return dict(schema=1, session=session_id, boot_sha256=elf_digest(Path("/proc/sys/kernel/random/boot_id")),
                cpu=cpus[0], kernel=platform.release(), protocol_sha256=elf_digest(anchor.PROTOCOL),
                implementation_sha256=implementation_hash(), probe_sha256=temporal.PROBE_SHA,
                reference_sha256=anchor.REFERENCE_SHA, invocation_sha256=invocation,
                elf_sha256=anchor.static_elf(elf), method=method)


def files_in(entry):
    files = {}
    for path in sorted(entry.rglob("*")):
        relative = path.relative_to(entry).as_posix()
        mode = path.lstat().st_mode
        require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), "unexpected cache member: " + relative)
        if relative != "cache.json" and stat.S_ISREG(mode):
            files[relative] = elf_digest(path)
    return files


def checked_entry(entry):
    raw = (entry / "cache.json").read_bytes()
    record = read_json(raw)
    require(not entry.is_symlink() and entry.name == object_hash(record["identity"])
            and record["files"] == files_in(entry), "cached preflight evidence changed")
    return record, sha(raw)


def paid_references(cache, refs, context):
    keys = []
    seen = set()
    for ref in refs:
        require(ref.parent == cache and digest(ref.name) and ref.name not in seen,
                "references must be distinct paid entries in this session")
        seen.add(ref.name)
        record, receipt = checked_entry(ref)
        require(all(record["identity"].get(k) == v for k, v in context.items())
                and "policy.json" in record["files"]
                and record["files"]["policy.json"] == record["files"]["selected-policy.json"],
                "reference is not a paid calibration from this context")
        keys.append({"key": ref.name, "receipt_sha256": receipt})
    return keys


def prepare(payload, probe, elf, stdin, cache, refs=(), method="footprint"):
    """Prepare from a pinned process main thread. Return hit/key and paths for fresh enforcement."""
    payload, probe, elf, stdin = (Path(p).resolve() for p in (payload, probe, elf, stdin))
    cache = Path(cache).absolute()
    refs = [Path(ref).absolute() for ref in refs]
    context = session_context(cache, elf, method)
    identity = {**context, "stdin_sha256": elf_digest(stdin), "references": paid_references(cache, refs, context)}
    key = object_hash(identity)
    entry = cache / key
    # Each digest has its own permanent lock. Crash-orphaned staging directories never become hits.
    with (cache / (key + ".lock")).open("a+b") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        require(os.sched_getaffinity(0) == {context["cpu"]}, "CPU changed while waiting for the preflight")
        hit = entry.exists()
        if hit:
            combined.verify_tools(payload, probe)
            record, _ = checked_entry(entry)
            require(record["identity"] == identity, "cache entry belongs to a different invocation")
        else:
            with tempfile.TemporaryDirectory(dir=cache, prefix=".building-") as tmp:
                draft = Path(tmp) / "capture"
                combined.capture(payload, probe, elf, stdin, draft)
                marker = read_json((draft / "combined.json").read_bytes())
                require(marker["stdin_sha256"] == identity["stdin_sha256"]
                        and marker["elf_sha256"] == context["elf_sha256"], "inputs changed during preflight")
                choice = select_policy.resolve(elf, draft, refs, temporal.PROBE_SHA, method)
                anchor.save_new(draft / "selection.json", choice)
                selected = draft if choice["action"] == "calibrate" else refs[choice["reference_index"]]
                shutil.copyfile(selected / "policy.json", draft / "selected-policy.json")
                files = files_in(draft)
                require(files["selected-policy.json"] == choice["policy_sha256"]
                        and paid_references(cache, refs, context) == identity["references"]
                        and os.sched_getaffinity(0) == {context["cpu"]},
                        "calibration bank, selected policy or CPU changed during preflight")
                anchor.save_new(draft / "cache.json", dict(identity=identity, files=files))
                draft.rename(entry)
    require(os.sched_getaffinity(0) == {context["cpu"]}, "CPU changed during preflight")
    return dict(hit=hit, key=key, capture=entry, selection=entry / "selection.json",
                policy=entry / "selected-policy.json")


def enforce(payload, probe, elf, stdin, cache, refs, method, out, limit_ms, timeout_ms, poll_ms):
    payload, elf, stdin, out = (Path(p).resolve() for p in (payload, elf, stdin, out))
    require(not out.is_relative_to(cache.resolve()), "live output must be outside the preflight cache")
    out.mkdir(mode=0o700)
    try:
        require(all(type(n) is int and n > 0 for n in (limit_ms, timeout_ms, poll_ms)),
                "limits and poll interval must be positive whole milliseconds")
        prepared = prepare(payload, probe, elf, stdin, cache, refs, method)
        anchor.save_new(out / "cache-use.json", {k: str(v) if isinstance(v, Path) else v for k, v in prepared.items()})
        result = run.run(payload, prepared["selection"], prepared["capture"], prepared["policy"],
                         elf, stdin, out / "enforced", limit_ms, timeout_ms, poll_ms)
    except (OSError, ValueError, KeyError, IndexError, OverflowError, TypeError) as error:
        result = dict(schema=1, authority="diagnostic_only", status="unavailable", pid=None, reason=str(error))
    anchor.save_new(out / "result.json", result)
    return result


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__, epilog="Use only operator-owned immutable inputs and runtime, "
                                 "and programs whose behavior depends solely on their ELF and stdin.")
    for flag in ("payload", "probe", "elf", "stdin", "cache", "out"):
        cli.add_argument("--" + flag, type=Path, required=True)
    cli.add_argument("--reference", action="append", type=Path, default=[], help="earlier paid cache capture, oldest first")
    cli.add_argument("--method", choices=select_policy.METHODS, default="footprint")
    cli.add_argument("--limit-ms", type=int, required=True)
    cli.add_argument("--timeout-ms", type=int, default=120000)
    cli.add_argument("--poll-ms", type=int, default=5)
    args = cli.parse_args()
    try:
        result = enforce(args.payload, args.probe, args.elf, args.stdin, args.cache, args.reference, args.method,
                         args.out, args.limit_ms, args.timeout_ms, args.poll_ms)
    except (ValueError, OSError) as error:
        result = dict(schema=1, authority="diagnostic_only", status="unavailable", pid=None, reason=str(error))
    print(json.dumps(result))
    raise SystemExit(live.EXIT[result["status"]])
