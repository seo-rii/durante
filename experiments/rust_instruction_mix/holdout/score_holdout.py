#!/usr/bin/env python3
"""Score the frozen recurrence clock and compare its two measurement modes on unseen Rust programs."""
import argparse
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import sys

from oracles import manifest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from score import LATENCY, PPM, STATIC, center, digest, ppm, relative_spread, require, uint

PROGRAMS = ('gcd', 'lower_bound', 'rolling_window')
WEIGHTS = {'instructions': 80, 'loop_recurrence_cycles_x16': 13, 'denominator': 1000}
PROBES = ('full', 'fast')
RECURRENCE = 'loop_recurrence_cycles_x16'


def validate_wait4(obs, expected_stdout, pids):
    require(uint(obs['pid'], 1) and obs['pid'] not in pids, 'invalid or repeated child PID')
    require(uint(obs['cpu_time_ns'], 1) and uint(obs['user_ns']) and uint(obs['system_ns'])
            and obs['cpu_time_ns'] == obs['user_ns'] + obs['system_ns'] and uint(obs['wall_ns'], 1),
            'invalid wait4 measurements')
    require(uint(obs['exit_code']) and obs['exit_code'] == 0 and obs['stdout_sha256'] == expected_stdout,
            'nonzero exit or unexpected stdout')
    pids.add(obs['pid'])


def features_for(obs, mode):
    production = obs['production']
    require(production['complete'] is True and production['valid'] is True
            and production['quality'] == 'uninterrupted' and production['cpuid_profile'] == 'x86-64-v2'
            and all(uint(production[k]) and production[k] == 1 for k in ('process_count', 'image_count', 'threads'))
            and all(uint(production[k]) and production[k] == 0 for k in ('unsupported_rep_entries', 'signals_observed')),
            'invalid ordinary meter receipt')
    require(all(uint(production[k]) for k in ('rep_bytes', 'rep_movs_bytes', 'rep_stos_bytes'))
            and production['rep_bytes'] == production['rep_movs_bytes'] + production['rep_stos_bytes'],
            'inconsistent ordinary REP counters')
    features = obs['features']
    instructions = production['instructions']
    require(uint(instructions, 1) and uint(features[RECURRENCE]), 'invalid predictor counts')
    if mode == 'full':
        require(set(features) == set(STATIC + LATENCY + ('bpred', 'cbr_executed', 'mispredicts'))
                and all(uint(features[k]) for k in STATIC + LATENCY + ('cbr_executed', 'mispredicts'))
                and features['instructions'] == instructions and all(features[k] <= instructions for k in STATIC[1:])
                and sum(features[k] for k in STATIC[3:6]) <= instructions
                and features['bound_cycles_x16'] >= max(4*instructions, features[RECURRENCE])
                and features['bpred'] is False
                and features['mispredicts'] == features['cbr_executed'] == 0,
                'full probe disagrees with ordinary instructions or active probe configuration')
    else:
        require(set(features) == {'version', RECURRENCE} and uint(features['version']) and features['version'] == 1,
                'invalid recurrence-only probe schema')
    return instructions, features[RECURRENCE]


def measurement_groups(data, plan, fixtures):
    require(data['complete'] is True and data['schema'] == 1 and data['cpu'] == 1
            and uint(data['collector_pid'], 1), 'expected a complete, CPU-pinned holdout dataset')
    require(data['tools'] == plan['tools'], 'tool fingerprints differ from frozen protocol')
    require(plan['weights'] == WEIGHTS and tuple(plan['programs']) == PROGRAMS and plan['repetitions'] == 3
            and plan['round_rotations'] == [0, 1, 2] and plan['full_first_indices'] == [0, 2, 3, 6, 7],
            'unexpected frozen clock or schedule')
    require(set(data['programs']) == set(PROGRAMS) == set(fixtures['programs']) and len(data['samples']) == 9,
            'missing program or measurement round')
    cases = {c['workload_id']: c for c in plan['cases']}
    require(len(plan['cases']) == len(cases) == 3 and set(cases) == set(PROGRAMS), 'wrong protocol cases')
    expected = [(name, rep) for rep, offset in enumerate(plan['round_rotations'], 1)
                for name in PROGRAMS[offset:] + PROGRAMS[:offset]]
    groups = {name: [] for name in PROGRAMS}
    seen = {data['collector_pid']}
    for index, (row, (name, rep)) in enumerate(zip(data['samples'], expected, strict=True)):
        require(row['workload_id'] == name and uint(row['repetition']) and row['repetition'] == rep
                and uint(row['scale']) and row['scale'] == 1, 'missing, repeated or out-of-order measurement')
        program, fixture = data['programs'][name], fixtures['programs'][name]
        require(digest(row['binary_sha256']) and row['binary_sha256'] == program['binary_sha256'] and
                row['source_sha256'] == program['source_sha256'] == fixture['source_sha256'] == cases[name]['source_sha256'],
                'changed executable or source identity')
        require(len(fixture['cases']) == 1 and all(row[k] == cases[name][k] == fixture['cases'][0][k]
                for k in ('input_sha256', 'expected_sha256')), 'changed input or oracle')
        modes = PROBES if index in plan['full_first_indices'] else PROBES[::-1]
        require(row['phase_order'] == ['native_before', *modes, 'native_after'], 'incorrect measured probe order')
        for phase in row['phase_order']:
            validate_wait4(row[phase], row['expected_sha256'], seen)
        groups[name].append(row)
    return groups


def score_family(samples, limits, weights):
    native = [row[phase]['cpu_time_ns'] for row in samples for phase in ('native_before', 'native_after')]
    native_median = center(native)
    native_spread = relative_spread(native)
    rounds = []
    timing_errors = {mode: [] for mode in PROBES}
    agreement_errors = []
    ratios = []
    for row in samples:
        counts = {mode: features_for(row[mode], mode) for mode in PROBES}
        estimates = {mode: Fraction(weights['instructions']*ins + weights[RECURRENCE]*rec, weights['denominator'])
                     for mode, (ins, rec) in counts.items()}
        first = row['phase_order'][1]
        detail = {'repetition': row['repetition'], 'first_probe': first}
        for mode in PROBES:
            error = estimates[mode] / native_median - 1
            timing_errors[mode].append(error)
            detail[mode] = {'instructions': counts[mode][0], RECURRENCE: counts[mode][1],
                            'prediction_ns': round(estimates[mode]), 'native_error_ppm': ppm(error),
                            'timing_pass': abs(error)*PPM <= limits['max_error_ppm'],
                            'probe_cpu_ns': row[mode]['cpu_time_ns']}
        delta = estimates['fast'] / estimates['full'] - 1
        agreement_errors.append(delta)
        ratio = Fraction(row['fast']['cpu_time_ns'], row['full']['cpu_time_ns'])
        ratios.append(ratio)
        detail.update({'fast_minus_full_prediction_ppm': ppm(delta),
                       'full_minus_fast_prediction_ppm': ppm(estimates['full'] / estimates['fast'] - 1),
                       'prediction_agreement_pass': abs(delta)*PPM <= limits['max_probe_prediction_delta_ppm'],
                       'fast_to_full_probe_cpu': round(float(ratio), 6)})
        for i, feature in enumerate(('instructions', RECURRENCE)):
            baseline, observed = counts['full'][i], counts['fast'][i]
            detail[f'{feature}_fast_minus_full'] = observed - baseline
            detail[f'{feature}_fast_minus_full_ppm'] = (ppm(Fraction(observed - baseline, baseline))
                                                       if baseline else (0 if observed == 0 else None))
        rounds.append(detail)
    outcome = {'native_min_ns': min(native), 'native_median_ns': float(native_median), 'native_max_ns': max(native),
               'controls_meeting_native_floor': sum(v >= limits['min_native_ns'] for v in native),
               'native_meets_floor': min(native) >= limits['min_native_ns'], 'native_spread_ppm': ppm(native_spread),
               'native_meets_spread_limit': native_spread*PPM <= limits['max_native_spread_ppm'],
               'predictions_agree': all(r['prediction_agreement_pass'] for r in rounds),
               'median_fast_minus_full_prediction_ppm': ppm(center(agreement_errors)),
               'worst_fast_minus_full_prediction_ppm': ppm(max(agreement_errors, key=abs)),
               'median_paired_fast_to_full_probe_cpu': round(float(center(ratios)), 6),
               'rounds': rounds}
    for mode in PROBES:
        errors = timing_errors[mode]
        outcome[mode] = {'timing_pass': all(r[mode]['timing_pass'] for r in rounds),
                         'median_signed_error_ppm': ppm(center(errors)), 'worst_signed_error_ppm': ppm(max(errors, key=abs)),
                         'median_probe_cpu_ns': int(center([r[mode]['probe_cpu_ns'] for r in rounds]))}
    return outcome


def evaluate(data, plan, fixtures):
    groups = measurement_groups(data, plan, fixtures)
    cases = {name: score_family(samples, plan['thresholds'], plan['weights']) for name, samples in groups.items()}
    rounds = [r for case in cases.values() for r in case['rounds']]
    summary = {'schema': 1, 'cases': cases, 'measured_rounds': len(rounds),
               'native_stable_cases': sum(c['native_meets_floor'] and c['native_meets_spread_limit'] for c in cases.values()),
               'probe_agreement_passing_rounds': sum(r['prediction_agreement_pass'] for r in rounds),
               'all_probes_agree': all(c['predictions_agree'] for c in cases.values())}
    for mode in PROBES:
        first = [r for r in rounds if r['first_probe'] == mode]
        summary[mode] = {'passed_cases': sum(c[mode]['timing_pass'] for c in cases.values()),
                         'all_cases_pass': all(c[mode]['timing_pass'] for c in cases.values()),
                         'passed_observations': sum(r[mode]['timing_pass'] for r in rounds),
                         'first_in_rounds': len(first), 'total_probe_cpu_ns': sum(r[mode]['probe_cpu_ns'] for r in rounds),
                         'median_paired_fast_to_full_probe_cpu_when_first': round(float(center([
                             Fraction(r['fast']['probe_cpu_ns'], r['full']['probe_cpu_ns']) for r in first])), 6)}
    summary['pooled_fast_to_full_probe_cpu'] = round(summary['fast']['total_probe_cpu_ns'] /
                                                     summary['full']['total_probe_cpu_ns'], 6)
    return summary


def sha(content):
    return hashlib.sha256(content).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='recorded JSON dataset (plain or gzip)')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--verify', type=Path, help='compare the replay with an archived evaluation')
    group.add_argument('--output', type=Path, help='create a new evaluation file')
    args = parser.parse_args()
    raw = args.dataset.read_bytes()
    if raw.startswith(b'\x1f\x8b'):
        raw = gzip.decompress(raw)
    data = json.loads(raw)
    protocol_bytes = (HERE/'protocol.json').read_bytes()
    fixtures_bytes = (HERE/'fixtures.json').read_bytes()
    protocol, fixtures = map(json.loads, (protocol_bytes, fixtures_bytes))
    require(data['protocol_sha256'] == sha(protocol_bytes) and data['fixtures_sha256'] ==
            protocol['fixtures_sha256'] == sha(fixtures_bytes), 'frozen protocol or fixture digest mismatch')
    require(manifest() == fixtures, 'published source or independently calculated input/output differs')
    require(data['compiler']['version'] == fixtures['compiler_version'] and
            data['compiler']['flags'] == fixtures['compiler_flags'], 'compiler identity mismatch')
    require(data['collector_sha256'] == sha((HERE/'collect_holdout.py').read_bytes()) and
            data['shared_collector_sha256'] == sha((HERE.parent/'collect.py').read_bytes()),
            'collector sources differ from those used for measurement')
    result = evaluate(data, protocol, fixtures)
    result.update(dataset_sha256=sha(raw), protocol_sha256=data['protocol_sha256'], fixtures_sha256=data['fixtures_sha256'])
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == result, 'archived evaluation differs from replay')
        print('Verified all nine holdout rounds, both counter modes and the frozen clock.')
    elif args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, sort_keys=True, indent=2)
            stream.write('\n')
    else:
        print(json.dumps(result, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
