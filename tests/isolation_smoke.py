#!/usr/bin/env python3
"""Trusted, finite namespace-profile fixtures inside an already bounded cgroup."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
from concurrent_spawn import cleanup_orphans, own_subreaper


def run_bounded(command, directory, timeout_ms, input_file=None):
    # Pipes expose bytes, not host regular-file/directory capabilities, to the
    # guest. These fixed fixtures produce bounded output; this is not a generic
    # untrusted-output collector for a public service.
    directory.mkdir(mode=0o700)
    started = time.monotonic()
    timed_out = False
    with subprocess.Popen(command, stdin=input_file if input_file is not None else subprocess.DEVNULL, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE) as process:
        try:
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
        for mode in ('probe', 'python', 'kill-inner', 'sleep'):
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
                        and result['security'].get('counter_integrity') == 'application_mutable')
                if mode in ('probe', 'python'):
                    expected = 'contained\n' if mode == 'probe' else 'python-contained\n'
                    row['passed'] = (safe and row['exit_code'] == 0 and row['stdout'] == expected
                                     and result['execution']['state'] == 'complete'
                                     and result.get('untrusted_inner_report', {}).get('measurement', {}).get('valid') is True)
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
