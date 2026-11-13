#!/usr/bin/env python3
"""Fixed Docker-owned controller/resource failure checks; not a service runner."""
import argparse
import errno
import hashlib
import json
import os
from pathlib import Path
import selectors
import shutil
import signal
import subprocess
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
CASES = ("success", "controller-kill", "controller-stop", "pids", "storage")
BOUND = 65536


def read_json(path):
    with Path(path).open("rb") as stream:
        data = stream.read(BOUND + 1)
    if len(data) > BOUND:
        raise ValueError("oversized evidence")
    return json.loads(data)


def publish(path, value):
    temporary = path.with_suffix(".next")
    temporary.write_text(json.dumps(value, indent=2) + "\n")
    temporary.replace(path)


def unified_cgroup(text):
    lines = text.splitlines()
    if len(lines) != 1 or not lines[0].startswith("0::/"):
        raise ValueError("a visible unified cgroup v2 hierarchy is required")
    value = lines[0][3:]
    if any(part in (".", "..") for part in value.split("/")):
        raise ValueError("invalid cgroup path")
    return value


def has_accepted_counts(report):
    # A stopped job cannot salvage an aggregate from an inner report even if
    # an unrelated exit/counter field happens to look successful.
    if not isinstance(report, dict):
        return False
    measurement = report.get("measurement", {})
    return ("untrusted_inner_report" in report or
            isinstance(measurement, dict) and (measurement.get("valid") is True or
                                               "instructions" in measurement))


def case_errors(case, observation, report, cleanup):
    errors = []
    if report is not None:
        if not isinstance(report, dict):
            errors.append("report is not an object")
            report = {}
        for name in ("execution", "measurement", "untrusted_inner_report"):
            if name in report and not isinstance(report[name], dict):
                errors.append("malformed report field: " + name)
        inner = report.get("untrusted_inner_report")
        if isinstance(inner, dict) and not isinstance(inner.get("measurement", {}), dict):
            errors.append("malformed inner measurement")
        if errors:
            report = {}
    if not cleanup:
        errors.append("external owner did not verify complete cgroup teardown")
    if observation.get("controller_pid", 0) <= 1:
        errors.append("real outer controller was not launched")
    if case == "success":
        if (observation.get("exit_code") != 0 or not isinstance(report, dict) or
                report.get("execution", {}).get("state") != "complete" or
                report.get("execution", {}).get("cleanup_complete") is not True or
                report.get("execution", {}).get("scratch_cleanup_complete") is not True or
                report.get("untrusted_inner_report", {}).get("measurement", {}).get("valid") is not True or
                observation.get("stdout") != "owner-success\n"):
            errors.append("positive isolated execution control failed")
    else:
        if has_accepted_counts(report):
            errors.append("failure exposed accepted aggregate counts")
        if case == "controller-kill" and (observation.get("fault") != "controller-kill" or
                observation.get("exit_code") != -signal.SIGKILL or
                observation.get("fixture_ready") is not True):
            errors.append("SIGKILL was not observed on the running outer controller")
        if case == "controller-stop" and (observation.get("fault") != "controller-stop" or
                observation.get("controller_stopped") is not True or
                observation.get("fixture_ready") is not True):
            errors.append("SIGSTOP was not observed on the running outer controller")
        if case == "pids" and (observation.get("fault") != "pids" or
                observation.get("pids_events_max", 0) < 1 or
                not observation.get("stdout", "").startswith("owner-pids-exhausted:")):
            errors.append("bounded task exhaustion was not observed")
        if case == "storage" and (observation.get("fault") != "storage" or
                observation.get("storage_errno") != errno.ENOSPC or
                observation.get("exit_code") != 1 or observation.get("stdout") != ""):
            errors.append("full private scratch storage did not fail before execution")
    if isinstance(report, dict) and (report.get("authority") != "diagnostic_only" or
            report.get("integrity_verified") is not False):
        errors.append("report promoted diagnostic authority")
    return errors


def worker(case):
    """Trusted PID 1 fixture driver. Deliberately does NOT tear down the job."""
    evidence = Path("/evidence")
    workspace = Path("/tmp/input")
    workspace.mkdir(mode=0o700)
    shutil.copyfile("/validation/owner-probe", workspace / "Main")
    (workspace / "Main").chmod(0o700)
    observation = {"case": case, "controller_pid": None, "phase": "setup",
                   "fixture_ready": False, "stdout": "", "stderr": ""}
    if case == "storage":
        written = 0
        with Path("/scratch/finite-fill").open("wb", buffering=0) as stream:
            try:
                for _ in range(16):
                    written += stream.write(b"x" * (1 << 20))
            except OSError as error:
                if error.errno != errno.ENOSPC:
                    raise
                observation.update(fault="storage", storage_errno=error.errno,
                                   storage_written_bytes=written)
        if observation.get("storage_errno") != errno.ENOSPC:
            raise RuntimeError("the 8 MiB scratch quota did not bind")
    command = ["/validation/durante", "isolate", "--launcher", "/isolation-tool/bwrap",
               "--drrun", "/sdk/bin64/drrun", "--client", "/client/libdurante.so",
               "--workspace", str(workspace), "--report", "/evidence/report.json",
               "--timeout-ms", "20000", "--", "/work/Main",
               "success" if case in ("success", "storage") else "pids" if case == "pids" else "hold"]
    env = os.environ.copy()
    env["TMPDIR"] = "/scratch" if case == "storage" else "/tmp"
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env)
    observation.update(controller_pid=process.pid, phase="running")
    publish(evidence / "observation.json", observation)
    selector = selectors.DefaultSelector()
    for name, stream in (("stdout", process.stdout), ("stderr", process.stderr)):
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ, name)
    deadline = time.monotonic() + 35
    injected = False
    while time.monotonic() < deadline:
        for key, _ in selector.select(timeout=0.02):
            data = os.read(key.fileobj.fileno(), 4096)
            if not data:
                selector.unregister(key.fileobj)
            else:
                observation[key.data] += data.decode("utf-8", errors="replace")
                if len(observation[key.data]) > BOUND:
                    raise RuntimeError("fixture exceeded its evidence bound")
        ready = observation["stdout"] == "owner-running\n"
        if ready and not observation["fixture_ready"]:
            observation.update(fixture_ready=True, phase="ready")
            publish(evidence / "observation.json", observation)
        if case == "pids" and observation["stdout"].startswith("owner-pids-exhausted:"):
            maximum = dict(line.split() for line in Path("/sys/fs/cgroup/pids.events").read_text().splitlines())
            observation.update(fault="pids", phase="fault", pids_events_max=int(maximum["max"]))
            publish(evidence / "observation.json", observation)
            break
        request = evidence / "request.json"
        if ready and request.exists() and not injected:
            selected = read_json(request).get("fault")
            if selected != case or selected not in ("controller-kill", "controller-stop"):
                raise RuntimeError("unexpected fault request")
            os.kill(process.pid, signal.SIGKILL if selected == "controller-kill" else signal.SIGSTOP)
            injected = True
            observation["fault"] = selected
        if injected and case == "controller-stop":
            status = Path(f"/proc/{process.pid}/status").read_text()
            if any(line.startswith("State:\tT") for line in status.splitlines()):
                observation.update(controller_stopped=True, phase="fault")
                publish(evidence / "observation.json", observation)
                break
        # A killed controller may leave descendants holding these pipes. Its
        # death evidence must reach the outside owner without waiting for EOF.
        if process.poll() is not None and (case == "controller-kill" or not selector.get_map()):
            observation.update(exit_code=process.returncode, phase="finished")
            publish(evidence / "observation.json", observation)
            break
    else:
        observation.update(phase="worker_timeout")
        publish(evidence / "observation.json", observation)
    selector.close()
    # Keep PID 1 alive so killing only Durante does not masquerade as external
    # owner cleanup. The host deadline must stop/remove this entire container.
    # A separate finite failsafe still prevents indefinite test processes.
    time.sleep(10)
    return 1


def docker(*args, timeout=15, allow_failure=False):
    result = subprocess.run(["docker", *args], stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, check=False)
    if len(result.stdout) > BOUND or len(result.stderr) > BOUND:
        raise RuntimeError("Docker response exceeded inspection bound")
    if result.returncode and not allow_failure:
        raise RuntimeError("Docker operation failed: " + result.stderr.decode(errors="replace")[:1000])
    return result


def cgroup_state(path):
    try:
        events = dict(line.split() for line in (path / "cgroup.events").read_text().splitlines())
        processes = [int(value) for value in (path / "cgroup.procs").read_text().split()]
        return {"exists": True, "populated": events.get("populated"), "processes": processes}
    except FileNotFoundError:
        if path.exists():
            raise
        return {"exists": False, "populated": "0", "processes": []}


def stop_owned_container(identity, group):
    """Always attempt removal; successful fault coverage also needs a live job."""
    result = {"cleanup_complete": False, "container_removed": False,
              "owner_kill_succeeded": False, "live_before_owner_stop": False, "errors": []}
    try:
        state = json.loads(docker("inspect", "--format", "{{json .State}}", identity).stdout)
        if group is None and state.get("Running") and state.get("Pid", 0) > 1:
            job = unified_cgroup(Path(f"/proc/{state['Pid']}/cgroup").read_text())
            own = unified_cgroup(Path("/proc/self/cgroup").read_text())
            if job == "/" or own == job or own.startswith(job + "/"):
                raise RuntimeError("cannot verify separate owned job cgroup")
            group = Path("/sys/fs/cgroup") / job.lstrip("/")
        before = cgroup_state(group) if group is not None else {}
        result["cgroup_before_stop"] = before
        result["live_before_owner_stop"] = (state.get("Running") is True and
                                            before.get("populated") == "1" and bool(before.get("processes")))
        if not result["live_before_owner_stop"]:
            result["errors"].append("job already stopped before external owner enforcement")
        stopped = docker("kill", "--signal", "KILL", identity, allow_failure=True)
        result["owner_kill_succeeded"] = stopped.returncode == 0
        if stopped.returncode:
            result["errors"].append("external owner kill did not succeed")
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        result["errors"].append("external stop failed: " + str(error))
    finally:
        # Recover even when create/start/inspect/kill timed out. The caller uses
        # its unique owned name if create did not return a container ID.
        try:
            docker("rm", "--force", identity, allow_failure=True)
        except (OSError, RuntimeError, subprocess.SubprocessError) as error:
            result["errors"].append("external removal failed: " + str(error))
        try:
            missing = docker("inspect", identity, allow_failure=True)
            result["container_removed"] = missing.returncode != 0 and b"no such" in missing.stderr.lower()
            after = cgroup_state(group) if group is not None else {}
            result["cgroup_after"] = after
            result["cleanup_complete"] = (result["container_removed"] and
                                           after.get("populated") == "0" and after.get("processes") == [])
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            result["errors"].append("cannot verify external cleanup: " + str(error))
    return result


def execute_case(args, case):
    directory = args.output_dir / case
    directory.mkdir(mode=0o700)
    name = "durante-owner-" + uuid.uuid4().hex
    identifier = None
    group = None
    observation, report = {}, None
    errors = []
    cleaned = False
    started = time.monotonic()
    row = {"case": case, "owner_pid": os.getpid(), "owner_outside_job": False,
           "accepted_counts": False, "container_removed": False}
    try:
        mounts = [(ROOT, "/source", True), (args.validation_bin, "/validation", True),
                  (args.launcher.parent, "/isolation-tool", True), (args.sdk, "/sdk", True),
                  (args.client.parent, "/client", True), (directory, "/evidence", False)]
        command = ["create", "--name", name, "--network", "none", "--read-only",
                   "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "1g",
                   "--pids-limit", "32", "--cpus", "1", "--cgroupns", "private",
                   "--security-opt", "systempaths=unconfined", "--security-opt", "seccomp=unconfined",
                   "--security-opt", "apparmor=unconfined", "--log-driver", "none",
                   "--tmpfs", "/tmp:rw,exec,mode=1777,size=67108864",
                   "--tmpfs", "/scratch:rw,noexec,mode=1777,size=8388608",
                   "--env", "PYTHONDONTWRITEBYTECODE=1"]
        for source, target, readonly in mounts:
            command += ["--mount", f"type=bind,src={source},dst={target}" + (",readonly" if readonly else "")]
        command += ["--entrypoint", "/usr/bin/python3", args.image,
                    "/source/tests/execution_owner.py", "--worker", case]
        identifier = docker(*command).stdout.decode().strip()
        if len(identifier) != 64 or any(character not in "0123456789abcdef" for character in identifier):
            raise RuntimeError("unexpected container identity")
        row["container_id"] = identifier
        docker("start", identifier)
        state = json.loads(docker("inspect", "--format", "{{json .State}}", identifier).stdout)
        if not state["Running"] or state["Pid"] <= 1:
            raise RuntimeError("job container did not start")
        job_group = unified_cgroup(Path(f"/proc/{state['Pid']}/cgroup").read_text())
        owner_group = unified_cgroup(Path("/proc/self/cgroup").read_text())
        if job_group == "/" or job_group == owner_group or owner_group.startswith(job_group + "/"):
            raise RuntimeError("execution owner is inside the job cgroup")
        group = Path("/sys/fs/cgroup") / job_group.lstrip("/")
        if not (group / "cgroup.events").is_file():
            raise RuntimeError("host cannot inspect the job cgroup")
        row.update(owner_outside_job=True, job_cgroup=job_group)
        row["cgroup_before"] = cgroup_state(group)
        deadline = time.monotonic() + 15
        requested = False
        while time.monotonic() < deadline:
            evidence = directory / "observation.json"
            if evidence.exists():
                observation = read_json(evidence)
                if observation.get("phase") == "ready" and not requested:
                    publish(directory / "request.json", {"fault": case})
                    requested = True
                if observation.get("phase") in ("fault", "finished", "worker_timeout"):
                    break
            time.sleep(0.02)
        else:
            errors.append("external owner observation deadline expired")
        # External teardown is always necessary, including when an inner
        # controller exited cleanly. The owner does not trust its cleanup claim.
    except (OSError, ValueError, KeyError, RuntimeError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    finally:
        teardown = stop_owned_container(identifier or name, group)
        errors.extend(teardown.pop("errors"))
        row.update(teardown)
        cleaned = teardown["cleanup_complete"]
    report_path = directory / "report.json"
    if report_path.exists() and report_path.stat().st_size:
        try:
            report = read_json(report_path)
        except (OSError, ValueError) as error:
            errors.append("nonempty report is unreadable: " + str(error))
    errors.extend(case_errors(case, observation, report, cleaned and row["container_removed"]))
    row.update(passed=not errors, errors=errors, observation=observation, report=report,
               accepted_counts=case == "success" and not errors,
               cleanup_complete=cleaned, elapsed_seconds=round(time.monotonic() - started, 3),
               owner_outcome="complete" if case == "success" else case)
    publish(directory / "owner-result.json", row)
    return row


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", choices=CASES)
    parser.add_argument("--image")
    for name in ("validation-bin", "launcher", "sdk", "client", "output-dir"):
        parser.add_argument("--" + name, type=Path)
    args = parser.parse_args()
    if args.worker:
        try:
            return worker(args.worker)
        except Exception as error:
            publish(Path("/evidence/worker-error.json"),
                    {"error": type(error).__name__ + ": " + str(error)[:1000]})
            return 1
    if not args.image or any(getattr(args, name) is None for name in
                             ("validation_bin", "launcher", "sdk", "client", "output_dir")):
        parser.error("image, validation-bin, launcher, sdk, client and output-dir are required")
    if os.getuid() == 0:
        parser.error("run as the nonroot operator used for isolation")
    for name in ("validation_bin", "launcher", "sdk", "client", "output_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if args.launcher.name != "bwrap" or args.client.name != "libdurante.so":
        parser.error("prepared installation names bwrap and libdurante.so are required")
    requested_image = args.image
    image_id = docker("image", "inspect", "--format", "{{.Id}}", requested_image).stdout.decode().strip()
    if (len(image_id) != 71 or not image_id.startswith("sha256:") or
            any(character not in "0123456789abcdef" for character in image_id[7:])):
        parser.error("cannot establish the exact local Docker image identity")
    args.image = image_id  # A mutable image tag must not change between cases.
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    inputs = {path: ROOT / path for path in ("tests/execution_owner.py", "tests/execution_owner_probe.c")}
    inputs.update(runner=args.validation_bin / "durante", fixture=args.validation_bin / "owner-probe",
                  launcher=args.launcher, launcher_manifest=args.launcher.with_name("bwrap.durante-isolation.json"),
                  client=args.client, drrun=args.sdk / "bin64/drrun",
                  runtime_manifest=args.sdk / ".durante-runtime.json",
                  release_core=args.sdk / "lib64/release/libdynamorio.so")
    identities = {name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in inputs.items()}
    rows = [execute_case(args, case) for case in CASES]
    unchanged = all(hashlib.sha256(inputs[name].read_bytes()).hexdigest() == digest
                    for name, digest in identities.items())
    summary = {"schema": 1, "authority": "diagnostic_only", "integrity_verified": False,
               "passed": unchanged and all(row["passed"] for row in rows),
               "cases": len(rows), "checks_passed": sum(row["passed"] for row in rows),
               "cleanup_complete": all(row["cleanup_complete"] and row["container_removed"] for row in rows),
               "inputs_unchanged": unchanged, "input_sha256": identities, "rows": rows,
               "image_requested": requested_image, "image_id": image_id,
               "limitations": ["trusted fixed fixtures and local Docker daemon only",
                               "no external-owner crash, hostile daemon or host failure qualification",
                               "storage case exhausts private scratch before target launch",
                               "not a production execution owner or trusted counter"]}
    publish(args.output_dir / "results.json", summary)
    print(json.dumps({key: value for key, value in summary.items() if key not in ("rows", "input_sha256")}))
    return 0 if summary["passed"] and summary["cleanup_complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
