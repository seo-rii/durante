#!/usr/bin/env python3
"""Trusted, finite namespace-profile fixtures inside an already bounded cgroup."""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import time
from concurrent_spawn import cleanup_orphans, own_subreaper


def run_bounded(command, directory, timeout_ms, input_file=None, cancel=False):
    # Pipes expose bytes, not host regular-file/directory capabilities, to the
    # guest. These fixed fixtures produce bounded output; this is not a generic
    # untrusted-output collector for a public service.
    directory.mkdir(mode=0o700)
    started = time.monotonic()
    timed_out = False
    with subprocess.Popen(command, stdin=input_file if input_file is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        try:
            if cancel:
                # Once the wrapper exists the outer CLI has installed its
                # cancellation handlers. Touch only this harness's child PID.
                ready_until = time.monotonic() + 3
                children = Path(f'/proc/{process.pid}/task/{process.pid}/children')
                while process.poll() is None and time.monotonic() < ready_until:
                    if children.read_bytes().strip():
                        for _ in range(3):
                            process.send_signal(signal.SIGTERM)
                        break
                    time.sleep(0.005)
            stdout, stderr = process.communicate(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired:
            timed_out = True
            process.kill()
            stdout, stderr = process.communicate(timeout=5)
        code = process.returncode
    cleaned = cleanup_orphans()
    (directory / 'stdout.txt').write_bytes(stdout)
    (directory / 'stderr.txt').write_bytes(stderr)
    return {'exit_code': code, 'elapsed_s': time.monotonic() - started,
            'harness_timeout': timed_out, 'harness_cleanup_complete': cleaned,
            'stdout': stdout[:65536].decode(errors='replace'),
            'stderr': stderr[:65536].decode(errors='replace')}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('runner-bin', 'launcher', 'sdk', 'client', 'probe-binary', 'output-dir'):
        p.add_argument('--' + name, type=Path, required=True)
    args = p.parse_args()
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    output = args.output_dir.resolve()
    workspace = output / 'input'
    workspace.mkdir(mode=0o700)
    shutil.copyfile(args.probe_binary, workspace / 'Main')
    (workspace / 'Main').chmod(0o700)
    (workspace / 'Main.py').write_text(
        'import hashlib, subprocess, zlib\n'
        'data=b"a"*10000\n'
        'assert len(hashlib.sha256(data).hexdigest())==64\n'
        'assert zlib.decompress(zlib.compress(data))==data\n'
        'assert subprocess.run(["/usr/bin/true"]).returncode==0\n'
        'print("python-contained")\n')
    secret = output / 'host-secret'
    secret.write_text('owned fixture data, not a host credential\n')
    pidns = os.readlink('/proc/self/ns/pid')
    netns = os.readlink('/proc/self/ns/net')
    previous_secret = os.environ.get('DURANTE_HOST_SECRET')
    os.environ['DURANTE_HOST_SECRET'] = 'owned-fixture-value'
    libc, previous = own_subreaper()
    rows = []
    try:
        for mode in ('probe', 'python', 'exit-139', 'signal-term', 'kill-inner', 'sleep'):
            case = output / mode
            report = case / 'report.json'
            timeout = 500 if mode == 'sleep' else 10000
            command = [str(args.runner_bin.resolve()), 'isolate', '--launcher', str(args.launcher.resolve()),
                       '--drrun', str(args.sdk.resolve() / 'bin64/drrun'), '--client', str(args.client.resolve()),
                       '--workspace', str(workspace), '--report', str(report), '--timeout-ms', str(timeout),
                       '--', '/work/Main', mode, str(secret), pidns, netns]
            if mode == 'python':
                command[command.index('--') + 1:] = ['/usr/bin/python3', '-I', '-S', '/work/Main.py']
            started = time.monotonic()
            row = run_bounded(command, case, timeout + 5000)
            row.update(case=mode, passed=False, elapsed_seconds=time.monotonic() - started)
            if report.is_file() and report.stat().st_size > 0:
                result = json.loads(report.read_text())
                row['report'] = result
                safe = (not row['harness_timeout'] and row['harness_cleanup_complete']
                        and result['execution'].get('cleanup_complete') is True
                        and result['execution'].get('scratch_cleanup_complete') is True
                        and result['authority'] == 'diagnostic_only'
                        and result.get('integrity_verified') is False
                        and result.get('measurement_contract', {}).get('version') == 1
                        and result['measurement_contract'].get('trusted_verdict_eligible') is False
                        and result['measurement_contract'].get('counter_integrity') == 'application_mutable'
                        and result['security'].get('counter_integrity') == 'application_mutable')
                if mode in ('probe', 'python'):
                    expected = 'contained\n' if mode == 'probe' else 'python-contained\n'
                    row['passed'] = (safe and row['exit_code'] == 0 and row['stdout'] == expected
                                     and result['execution']['state'] == 'complete'
                                     and result.get('untrusted_inner_report', {}).get('measurement', {}).get('valid') is True)
                elif mode == 'exit-139':
                    inner = result.get('untrusted_inner_report', {})
                    execution = inner.get('execution', {})
                    row['passed'] = (safe and row['exit_code'] == 0
                                     and row['stdout'] == 'exiting-139\n' and row['stderr'] == ''
                                     and result['execution']['state'] == 'complete'
                                     and result['execution'].get('wrapper_exit_code') == 0
                                     and execution.get('state') == 'complete'
                                     and execution.get('exit_code') == 139
                                     and execution.get('signal', 'missing') is None
                                     and inner.get('measurement', {}).get('valid') is True
                                     and inner.get('measurement', {}).get('complete') is True
                                     and 'untrusted_failure_execution' not in result)
                elif mode == 'signal-term':
                    execution = result.get('untrusted_failure_execution', {})
                    row['passed'] = (safe and row['exit_code'] == 1
                                     and row['stdout'] == 'terminating-self\n' and row['stderr'] == ''
                                     and result['execution']['state'] == 'isolation_failed'
                                     and result['execution'].get('wrapper_exit_code') == 1
                                     and 'untrusted_inner_report' not in result
                                     and set(execution) == {'state', 'root_pid', 'exit_code', 'signal'}
                                     and execution.get('state') == 'incomplete'
                                     and type(execution.get('root_pid')) is int and execution['root_pid'] > 0
                                     and execution.get('exit_code', 'missing') is None
                                     and execution.get('signal') == signal.SIGTERM)
                elif mode == 'kill-inner':
                    row['passed'] = (safe and row['exit_code'] == 1
                                     and 'untrusted_inner_report' not in result
                                     and row['stdout'] == 'killing-inner\n'
                                     and row['elapsed_seconds'] < 2)
                else:
                    row['passed'] = (safe and row['exit_code'] == 124
                                     and result['execution']['state'] == 'timed_out'
                                     and 'untrusted_inner_report' not in result)
            rows.append(row)
        # Reject an unsafe snapshot without ever opening a FIFO or following a
        # symlink. The only destinations are the harness's private sentinel.
        for kind in ('symlink', 'fifo'):
            bad = workspace / 'unsafe'
            if kind == 'symlink':
                bad.symlink_to(secret)
            else:
                os.mkfifo(bad, 0o600)
            case = output / kind
            command = [str(args.runner_bin.resolve()), 'isolate', '--launcher', str(args.launcher.resolve()),
                       '--drrun', str(args.sdk.resolve() / 'bin64/drrun'), '--client', str(args.client.resolve()),
                       '--workspace', str(workspace), '--report', str(case / 'report.json'), '--', '/work/Main']
            row = run_bounded(command, case, 5000)
            row.update(case=kind, passed=row['exit_code'] == 1 and not row['harness_timeout']
                       and row['harness_cleanup_complete'])
            rows.append(row)
            bad.unlink()
        # A read-only host file descriptor is still a capability: /proc/self/fd
        # can reopen its inode with broader permissions. Reject it before launch.
        case = output / 'regular-stdio'
        command = [str(args.runner_bin.resolve()), 'isolate', '--launcher', str(args.launcher.resolve()),
                   '--drrun', str(args.sdk.resolve() / 'bin64/drrun'), '--client', str(args.client.resolve()),
                   '--workspace', str(workspace), '--report', str(case / 'report.json'),
                   '--', '/work/Main', 'probe', str(secret), pidns, netns]
        with secret.open('rb') as data:
            row = run_bounded(command, case, 5000, input_file=data)
        row.update(case='regular-stdio', passed=row['exit_code'] == 1 and not row['harness_timeout']
                   and row['harness_cleanup_complete'] and row['stdout'] == '')
        rows.append(row)
        for mode, limit in [('capture-exact', 8192), ('capture-flood', 4096),
                            ('capture-hold', 8192), ('capture-close', 8192),
                            ('capture-cancel', 8192), ('capture-kill-inner', 8192)]:
            case = output / mode
            timeout = 500 if mode in ('capture-hold', 'capture-close') else 10000
            target_mode = {'capture-cancel': 'sleep', 'capture-kill-inner': 'kill-inner'}.get(mode, mode)
            command = [str(args.runner_bin.resolve()), 'isolate', '--launcher', str(args.launcher.resolve()),
                       '--drrun', str(args.sdk.resolve() / 'bin64/drrun'), '--client', str(args.client.resolve()),
                       '--workspace', str(workspace), '--report', str(case / 'report.json'),
                       '--timeout-ms', str(timeout), '--output-dir', str(case / 'captured'),
                       '--output-limit-bytes', str(limit),
                       '--', '/work/Main', target_mode, str(secret), pidns, netns]
            row = run_bounded(command, case, timeout + 5000, cancel=mode == 'capture-cancel')
            row.update(case=mode, passed=False)
            report = case / 'report.json'
            if report.is_file() and report.stat().st_size:
                result = json.loads(report.read_text())
                row['report'] = result
                captured = result.get('output', {})
                files = [case / 'captured' / name for name in ('stdout', 'stderr')]
                data = [file.read_bytes() for file in files] if all(file.is_file() for file in files) else []
                safe = (not row['harness_timeout'] and row['harness_cleanup_complete']
                        and result['execution'].get('cleanup_complete') is True
                        and result['execution'].get('scratch_cleanup_complete') is True
                        and result.get('integrity_verified') is False
                        and result['authority'] == 'diagnostic_only'
                        and result.get('measurement_contract', {}).get('version') == 1
                        and result.get('measurement_contract', {}).get('trusted_verdict_eligible') is False
                        and result.get('measurement_contract', {}).get('counter_integrity') == 'application_mutable'
                        and captured.get('published') is True and len(data) == 2
                        and sum(map(len, data)) <= limit
                        and captured.get('stdout_captured_bytes') == len(data[0])
                        and captured.get('stderr_captured_bytes') == len(data[1])
                        and row['stdout'] == '' and row['stderr'] == '')
                if mode == 'capture-exact':
                    row['passed'] = (safe and row['exit_code'] == 0 and captured.get('complete') is True
                                     and data == [b'\x00' + b'O' * 4095, b'\xff' + b'E' * 4095]
                                     and result.get('untrusted_inner_report', {}).get('measurement', {}).get('valid') is True)
                else:
                    expected_state, expected_code = {
                        'capture-flood': ('output_limit', 125),
                        'capture-hold': ('timed_out', 124), 'capture-close': ('timed_out', 124),
                        'capture-cancel': ('cancelled', 128 + signal.SIGTERM),
                        'capture-kill-inner': ('isolation_failed', 1)}[mode]
                    row['passed'] = (safe and row['exit_code'] == expected_code
                                     and result['execution']['state'] == expected_state
                                     and 'untrusted_inner_report' not in result)
                    if mode == 'capture-flood':
                        row['passed'] = (row['passed'] and captured.get('truncated') is True
                                         and captured.get('complete') is False and sum(map(len, data)) == limit
                                         and limit < captured['observed_bytes'] <= limit + 8192)
            rows.append(row)
        assert secret.read_text() == 'owned fixture data, not a host credential\n'
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
        if previous_secret is None:
            os.environ.pop('DURANTE_HOST_SECRET', None)
        else:
            os.environ['DURANTE_HOST_SECRET'] = previous_secret
    result = {'schema': 1, 'integrity_verified': False, 'passed': sum(row['passed'] for row in rows),
              'cases': len(rows), 'cleanup_complete': cleaned,
              'failures': [row['case'] for row in rows if not row['passed']], 'rows': rows}
    (output / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'rows'}))
    return 0 if cleaned and rows and all(row['passed'] for row in rows) else 1


if __name__ == '__main__':
    raise SystemExit(main())
