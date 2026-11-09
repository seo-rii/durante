#!/usr/bin/env python3
"""Bounded reference collector for diagnostic Workmeter process-tree records.

Never use these application-address-space counters as a security boundary.
The caller must finish and reap the complete target tree before collecting.
"""
import argparse
import json
import itertools
import os
from pathlib import Path
import re
import stat
import sys

MAX_PROCESSES = 256
MAX_FILE_BYTES = 1 << 20
MAX_TOTAL_BYTES = 16 << 20
MAX_EVENTS = 4096
MAX_LINE_BYTES = 3072
U64_MAX = (1 << 64) - 1
FIELDS = ('instructions', 'rep_bytes', 'rep_entries', 'unsupported_rep_entries', 'signals_observed')


class Invalid(ValueError):
    pass


def require(ok, message):
    if not ok:
        raise Invalid(message)


def uint(value, label, maximum=U64_MAX):
    require(type(value) is int and 0 <= value <= maximum, 'invalid ' + label)
    return value


def pid(value):
    return uint(value, 'pid', (1 << 31) - 1) > 0


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'duplicate JSON key')
        result[key] = value
    return result


def load_records(directory):
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    root = os.open(directory, flags)
    try:
        with os.scandir(root) as entries:
            names = sorted(entry.name for entry in itertools.islice(entries, MAX_PROCESSES + 1))
        require(0 < len(names) <= MAX_PROCESSES, 'process file count outside bound')
        streams = {}
        total = 0
        for name in names:
            match = re.fullmatch(r'process-([1-9][0-9]*)\.jsonl', name)
            require(match is not None, 'unexpected report entry')
            process = int(match[1]); require(pid(process), 'invalid filename pid')
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK, dir_fd=root)
            try:
                st = os.fstat(fd)
                require(stat.S_ISREG(st.st_mode) and st.st_nlink == 1, 'unsafe report file')
                require(0 < st.st_size <= MAX_FILE_BYTES, 'report size outside bound')
                total += st.st_size; require(total <= MAX_TOTAL_BYTES, 'total report size exceeded')
                data = b''
                while len(data) <= MAX_FILE_BYTES:
                    part = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - len(data)))
                    if not part:
                        break
                    data += part
                require(len(data) == st.st_size and data.endswith(b'\n'), 'incomplete or growing report')
                after = os.fstat(fd)
                require((after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_nlink) ==
                        (st.st_size, st.st_mtime_ns, st.st_ctime_ns, 1), 'report changed during collection')
            finally:
                os.close(fd)
            lines = data.splitlines()
            # The bound applies to a process stream, including all exec images.
            require(0 < len(lines) <= MAX_EVENTS, 'event count exceeded')
            records = []
            for line in lines:
                require(0 < len(line) < MAX_LINE_BYTES, 'event size outside bound')
                record = json.loads(line.decode('utf-8'), object_pairs_hook=unique_object,
                                    parse_constant=lambda _: (_ for _ in ()).throw(Invalid('nonfinite JSON')))
                require(type(record) is dict and type(record.get('pid')) is int and record['pid'] == process, 'record pid mismatch')
                records.append(record)
            streams[process] = records
        return streams
    finally:
        os.close(root)


def terminal(record, is_exec, rep_enabled):
    values = {key: uint(record.get(key), key) for key in FIELDS}
    require(record.get('valid') is True and record.get('accounting_error') is False,
            'invalid terminal accounting')
    started = uint(record.get('threads_started'), 'threads_started')
    ended = uint(record.get('threads_finished'), 'threads_finished')
    require(started > 0 and started == ended and record.get('threads_complete') is True,
            'incomplete threads')
    require(values['instructions'] > 0, 'zero work')
    require(record.get('quality') == ('signals_observed' if values['signals_observed'] else 'uninterrupted'),
            'signal quality mismatch')
    if not rep_enabled:
        require(values['rep_bytes'] == values['rep_entries'] == values['unsupported_rep_entries'] == 0,
                'REP counters without REP mode')
    code = uint(record.get('app_exit_code'), 'app_exit_code', 255)
    if not is_exec:
        require(record.get('app_exit_seen') is True, 'missing application exit')
    return {**values, 'threads': started, 'exit_code': None if is_exec else code}


def aggregate(streams, root_pid):
    require(pid(root_pid) and root_pid in streams, 'root PID absent')
    require(0 < len(streams) <= MAX_PROCESSES, 'process count exceeded')
    segments = set(); processes = {}; spawn_edges = {}; rep_mode = None
    total = {field: 0 for field in FIELDS}; images = 0; all_segments = []
    for process, records in streams.items():
        require(pid(process) and 0 < len(records) <= MAX_EVENTS, 'invalid process stream')
        state = None; pending_exec = None; ended = False; first = None; summaries = []
        sequence = 0
        for record in records:
            require(type(record.get('schema')) is int and record['schema'] == 1 and
                    type(record.get('client_version')) is int and record['client_version'] == 1 and
                    record.get('authority') == 'diagnostic_only' and
                    record.get('metric') == 'bb_entry_app_instructions', 'record version/metric mismatch')
            require(type(record.get('pid')) is int and record['pid'] == process, 'PID mismatch')
            event = record.get('event'); segment = record.get('segment')
            require(type(segment) is str and re.fullmatch(str(process) + r'-[0-9]+', segment), 'invalid segment ID')
            if event == 'start':
                require(not ended and segment not in segments, 'duplicate segment or reused PID')
                require(type(record.get('seq')) is int and record['seq'] == 1, 'start sequence must be one')
                mode = record.get('rep_bytes_enabled')
                require(type(mode) is bool and record.get('sdk_version') == '11.3.0', 'start metadata mismatch')
                if rep_mode is None: rep_mode = mode
                require(rep_mode == mode, 'mixed REP modes')
                origin = record.get('origin'); parent = record.get('parent_pid'); parent_segment = record.get('parent_segment')
                if state is None:
                    first = record
                    if process == root_pid:
                        require(origin == 'initial' and type(parent) is int and parent == 0 and parent_segment == '', 'invalid root origin')
                    else:
                        require(origin == 'fork' and pid(parent) and type(parent_segment) is str, 'unlinked process origin')
                else:
                    require(pending_exec is not None, 'exec image without checkpoint')
                    require(origin == 'initial' and type(parent) is int and parent == 0 and parent_segment == '', 'invalid exec origin')
                    summaries.append((state, terminal(pending_exec, True, rep_mode)))
                state = segment; segments.add(segment); sequence = 1; pending_exec = None
                continue
            require(state is not None and not ended and segment == state, 'event outside live segment')
            sequence += 1
            require(type(record.get('seq')) is int and record['seq'] == sequence, 'event sequence gap or duplicate')
            if event == 'spawn':
                child = record.get('child_pid')
                require(pid(child) and child != process and child not in spawn_edges, 'duplicate/invalid child edge')
                require(record.get('supported') is True, 'unsupported process creation')
                require(type(record.get('syscall')) is int and record['syscall'] in (56, 57, 58, 435), 'invalid spawn syscall')
                spawn_edges[child] = (process, state)
            elif event == 'exec_attempt':
                require(pending_exec is None, 'concurrent or missing exec result')
                pending_exec = record
            elif event == 'exec_failed':
                require(pending_exec is not None and type(record.get('result')) is int and record['result'] < 0,
                        'invalid failed exec')
                require(record.get('syscall') in (59, 322), 'invalid exec syscall')
                pending_exec = None
            elif event == 'end':
                require(pending_exec is None, 'exec outcome missing')
                summaries.append((state, terminal(record, False, rep_mode)))
                ended = True
            else:
                raise Invalid('unsupported or interrupted lifecycle event')
        require(ended and first is not None, 'process has no completed final image')
        process_totals = {field: 0 for field in FIELDS}
        for segment, values in summaries:
            images += 1
            for field in FIELDS:
                require(total[field] <= U64_MAX - values[field], 'tree counter overflow')
                total[field] += values[field]; process_totals[field] += values[field]
            all_segments.append({'pid': process, 'segment': segment, **values})
        processes[process] = {'pid': process, 'parent_pid': first['parent_pid'],
            'parent_segment': first['parent_segment'], 'images': len(summaries),
            'exit_code': summaries[-1][1]['exit_code'], **process_totals}
    require(set(spawn_edges) == set(processes) - {root_pid}, 'missing or unannounced child process')
    for child, (parent, parent_segment) in spawn_edges.items():
        require(parent in processes and processes[child]['parent_pid'] == parent and
                processes[child]['parent_segment'] == parent_segment, 'child lineage mismatch')
    # All nodes must be reachable from the root; reject disconnected cycles.
    visited = {root_pid}
    for _ in processes:
        visited |= {child for child, (parent, _) in spawn_edges.items() if parent in visited}
    require(visited == set(processes), 'disconnected process graph')
    return {'schema': 1, 'authority': 'diagnostic_only', 'metric': 'bb_entry_app_instructions',
            'scope': 'process_tree_user_code', 'valid': True, 'complete': True,
            'root_pid': root_pid, 'process_count': len(processes), 'image_count': images,
            'rep_bytes_enabled': rep_mode, 'quality': 'signals_observed' if total['signals_observed'] else 'uninterrupted',
            **total, 'processes': [processes[p] for p in sorted(processes)], 'segments': all_segments,
            'limits': {'max_processes': MAX_PROCESSES, 'max_file_bytes': MAX_FILE_BYTES,
                       'max_total_bytes': MAX_TOTAL_BYTES, 'max_events_per_process': MAX_EVENTS}}


def collect(directory, root_pid):
    try:
        return aggregate(load_records(directory), root_pid)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as error:
        return {'schema': 1, 'authority': 'diagnostic_only', 'metric': 'bb_entry_app_instructions',
                'scope': 'process_tree_user_code', 'valid': False, 'complete': False,
                'root_pid': root_pid, 'reason': str(error)[:300]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dir', required=True, type=Path)
    parser.add_argument('--root-pid', required=True, type=int)
    args = parser.parse_args()
    result = collect(args.dir, args.root_pid)
    print(json.dumps(result, allow_nan=False))
    return 0 if result['valid'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
