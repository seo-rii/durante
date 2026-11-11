#!/usr/bin/env python3
"""Bounded tests of owned helper startup death and trusted PID 1 protection.

Only helpers created by this process are signalled. A held setup pipe prevents
the startup-death payload from running; every surviving namespace is killed and
reaped by this subreaper harness before it returns.
"""
import argparse
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

from concurrent_spawn import cleanup_orphans, own_subreaper

PROBE = '''import ctypes,json,os
result={"pid":os.getpid(),"parent":os.getppid(),"dumpable":ctypes.CDLL(None).prctl(3,0,0,0,0)}
for name,path in [("init_mem","/proc/1/mem"),("init_fds","/proc/1/fd")]:
 try:
  if name=="init_fds":os.listdir(path)
  else:
   fd=os.open(path,os.O_RDWR);os.close(fd)
  result[name+"_denied"]=False
 except PermissionError:result[name+"_denied"]=True
leaked=[]
for name in os.listdir("/proc/self/fd"):
 try:
  if "pidfd" in os.readlink("/proc/self/fd/"+name):leaked.append(name)
 except FileNotFoundError:pass
result["pidfd_leaked"]=bool(leaked)
print(json.dumps(result))
'''


def options(binary):
    command = [str(binary), '--unshare-user', '--unshare-pid', '--unshare-net',
               '--unshare-ipc', '--unshare-uts', '--disable-userns',
               '--assert-userns-disabled', '--cap-drop', 'ALL', '--die-with-parent',
               '--new-session', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin']
    for path in ['/usr/bin', '/usr/lib', '/usr/lib64']:
        if Path(path).is_dir():
            command += ['--ro-bind', path, path]
    command += ['--symlink', 'usr/bin', '/bin', '--symlink', 'usr/lib', '/lib',
                '--symlink', 'usr/lib64', '/lib64', '--proc', '/proc', '--dev', '/dev']
    return command


def child_info(fd, process):
    deadline, data = time.monotonic() + 5, b''
    while time.monotonic() < deadline:
        ready = select.select([fd], [], [], 0.05)[0]
        if ready:
            chunk = os.read(fd, 4096 - len(data))
            data += chunk
            try:
                return json.loads(data)['child-pid']
            except (ValueError, KeyError):
                if not chunk or len(data) >= 4096:
                    break
        if process.poll() is not None:
            break
    raise AssertionError('helper did not publish a bounded startup PID record')


def startup_death(binary, work, index, expect_guard):
    block_read, block_write = os.pipe2(os.O_CLOEXEC)
    info_read, info_write = os.pipe2(os.O_CLOEXEC)
    process, namespace = None, None
    result = {'case': f'startup-parent-death-{index}', 'expected_guard': expect_guard}
    output = work / (result['case'] + '.stderr')
    try:
        with output.open('wb') as stderr:
            command = options(binary) + ['--block-fd', str(block_read), '--info-fd',
                                       str(info_write), '--', '/usr/bin/sleep', '0.1']
            process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=stderr,
                                       pass_fds=(block_read, info_write), start_new_session=True)
            os.close(block_read); block_read = -1
            os.close(info_write); info_write = -1
            pid = child_info(info_read, process)
            if not isinstance(pid, int) or pid <= 1:
                raise AssertionError('invalid helper namespace PID')
            namespace = os.pidfd_open(pid, 0)
            # The private namespace is still blocked before its payload fork.
            # pidfd binds this exact owned monitor instance, avoiding PID reuse.
            monitor = os.pidfd_open(process.pid, 0)
            try:
                signal.pidfd_send_signal(monitor, signal.SIGKILL)
            finally:
                os.close(monitor)
            result['monitor_exit'] = process.wait(timeout=2)
            poll = select.poll(); poll.register(namespace, select.POLLIN)
            died = bool(poll.poll(400))
            result['namespace_exit_observed'] = died
            result['passed'] = died if expect_guard else not died
    finally:
        if namespace is not None:
            try:
                signal.pidfd_send_signal(namespace, signal.SIGKILL)
            except ProcessLookupError:
                pass
            os.close(namespace)
        if process is not None and process.poll() is None:
            process.kill(); process.wait(timeout=2)
        for fd in [block_read, block_write, info_read, info_write]:
            if fd >= 0:
                os.close(fd)
        result['cleanup_complete'] = cleanup_orphans()
    result['passed'] = result.get('passed', False) and result['cleanup_complete']
    return result


def protected_init(binary):
    process = subprocess.run(options(binary) + ['--', '/usr/bin/python3', '-I', '-S', '-c', PROBE],
                             stdin=subprocess.DEVNULL, capture_output=True, timeout=5)
    if process.returncode != 0 or len(process.stdout) > 4096:
        raise AssertionError('protected-init fixture failed (exit=' + str(process.returncode)
                             + '): ' + process.stderr[:2048].decode('utf-8', errors='replace'))
    result = json.loads(process.stdout)
    result['case'] = 'protected-init-and-no-monitor-fd'
    result['passed'] = (result['pid'] == 2 and result['parent'] == 1 and result['dumpable'] == 1
                        and result['init_mem_denied'] and result['init_fds_denied']
                        and not result['pidfd_leaked'])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--launcher', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, help='optional old helper demonstrating the startup window')
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--repeats', type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.repeats <= 20:
        parser.error('repeats must be between 1 and 20')
    args.output_dir.mkdir(mode=0o700, parents=True, exist_ok=False)
    libc, previous = own_subreaper()
    rows, failure = [], None
    try:
        rows.append(protected_init(args.launcher.resolve()))
        for index in range(args.repeats):
            rows.append(startup_death(args.launcher.resolve(), args.output_dir, index, True))
        if args.baseline:
            baseline = args.output_dir / 'baseline'; baseline.mkdir(mode=0o700)
            rows.append(startup_death(args.baseline.resolve(), baseline, 0, False))
    except (AssertionError, OSError, ValueError, subprocess.SubprocessError) as error:
        failure = str(error)
    finally:
        cleaned = cleanup_orphans()
        libc.prctl(36, previous, 0, 0, 0)
    summary = {'schema': 1, 'passed': failure is None and cleaned and bool(rows)
               and all(row['passed'] for row in rows), 'cleanup_complete': cleaned,
               'rows': rows, 'failure': failure}
    (args.output_dir / 'results.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))
    return 0 if summary['passed'] else 1


if __name__ == '__main__':
    sys.exit(main())
