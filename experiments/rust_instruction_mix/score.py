#!/usr/bin/env python3
"""Replay the prespecified Rust instruction-class fits from complete, paired triplets."""
import argparse
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import re
from statistics import median


HERE = Path(__file__).resolve().parent
STATIC = ('instructions', 'loads', 'stores', 'cond_branches', 'direct_jumps_calls',
          'indirect_branches', 'muls', 'divs', 'fp_ops')
LATENCY = ('critical_path_cycles_x16', 'loop_recurrence_cycles_x16', 'bound_cycles_x16')
PPM = 1_000_000


def require(condition, message):
    if not condition:
        raise ValueError(message)


def uint(value, minimum=0):
    return type(value) is int and minimum <= value < 2**64


def digest(value):
    return isinstance(value, str) and re.fullmatch('[0-9a-f]{64}', value) is not None


def ppm(value):
    return round(value * PPM)


def center(values):
    return median([Fraction(value) for value in values])


def relative_spread(values):
    middle = center(values)
    if middle:
        return Fraction(max(values) - min(values), middle)
    return None if max(values) else Fraction(0)


def fit(training, columns):
    """Solve one- or two-variable NNLS exactly, including collinear and zero columns."""
    x = [[Fraction(counts[name], native) for name in columns] for counts, native in training]
    sums = [sum(col) for col in zip(*x)]
    norms = [sum(v*v for v in col) for col in zip(*x)]
    # The axis optima suffice on the boundary. The first axis is the prespecified tiebreak.
    candidates = []
    for i, norm in enumerate(norms):
        if norm:
            axis = [Fraction(0)] * len(columns)
            axis[i] = sums[i] / norm
            candidates.append(axis)
    rank = int(any(norms))
    if len(columns) == 2:
        product = sum(a*b for a, b in x)
        det = norms[0]*norms[1] - product**2
        if det:
            rank = 2
            interior = [(sums[0]*norms[1] - sums[1]*product) / det,
                        (sums[1]*norms[0] - sums[0]*product) / det]
            if min(interior) >= 0:
                candidates.append(interior)
    candidates.append([Fraction(0)] * len(columns))

    def squared_relative_loss(weights):
        return sum((sum(a*b for a, b in zip(row, weights, strict=True)) - 1)**2 for row in x)

    weights = min(candidates, key=squared_relative_loss)
    return dict(zip(columns, weights, strict=True)), rank


def collect_cases(data, protocol):
    require(data['schema'] == 1 and data['complete'] is True and data['cpu'] == 1,
            'expected a completed CPU 1 dataset')
    require(tuple(protocol['static_features']) == STATIC, 'unexpected static feature contract')
    require(set(data['tools']) == {'launcher', 'meter', 'runtime', 'probe', 'production_reader'}
            and all(digest(h) for h in data['tools'].values()) and data['tools']['meter'] == protocol['meter_sha256'],
            'invalid tool hashes or wrong ordinary client')
    planned = {(c['workload_id'], c['scale']): c for c in protocol['cases']}
    order = [(name, scale) for name in protocol['programs'] for scale in protocol['scales']]
    require(len(planned) == len(order) == len(protocol['cases']) == 15 and set(order) == set(planned),
            'invalid case set in protocol')
    for (name, _), case in planned.items():
        require(case['split'] in ('train', 'validation') and name in protocol[case['split']],
                'inconsistent split in protocol')
    sequence = [(name, scale, rep) for rep, offset in enumerate(protocol['round_rotations'], 1)
                for name, scale in order[offset:] + order[:offset]]
    require(len(data['samples']) == len(sequence) == 45, 'missing or duplicate triplets')
    programs = data['programs']
    require(set(programs) == set(protocol['programs']), 'invalid compiled program set')
    require(all(digest(p['binary_sha256']) for p in programs.values()), 'invalid binary digest')
    groups = {key: [] for key in order}
    require(uint(data['collector_pid'], 1), 'invalid collector PID')
    seen_pids = {data['collector_pid']}
    for row, expected in zip(data['samples'], sequence, strict=True):
        name, scale, repetition = expected
        key = name, scale
        require(uint(row['scale'], 1) and uint(row['repetition'], 1)
                and (row['workload_id'], row['scale'], row['repetition']) == expected,
                f'{expected}: missing, duplicate or out-of-order triplet')
        case = planned[key]
        require(all(row[h] == case[h] for h in ('expected_sha256', 'input_sha256', 'source_sha256'))
                and case['source_sha256'] == programs[name]['source_sha256']
                and row['binary_sha256'] == programs[name]['binary_sha256'], f'{expected}: artifact or fixture changed')
        for phase in ('native_before', 'metered', 'native_after'):
            obs = row[phase]
            require(uint(obs['cpu_time_ns'], 1) and uint(obs['user_ns']) and uint(obs['system_ns'])
                    and obs['cpu_time_ns'] == obs['user_ns'] + obs['system_ns'] and uint(obs['wall_ns'], 1)
                    and uint(obs['exit_code']) and obs['exit_code'] == 0, f'{expected} {phase}: invalid wait4 receipt')
            require(uint(obs['pid'], 1) and obs['pid'] not in seen_pids, f'{expected} {phase}: duplicate or invalid PID')
            require(obs['stdout_sha256'] == case['expected_sha256'], f'{expected} {phase}: stdout mismatch')
            seen_pids.add(obs['pid'])
        feature = row['metered']['features']
        require(all(uint(feature[name]) for name in STATIC + LATENCY), f'{expected}: invalid counters')
        instructions = feature['instructions']
        require(uint(instructions, 1) and all(feature[k] <= instructions for k in STATIC[1:])
                and sum(feature[k] for k in STATIC[3:6]) <= instructions
                and feature['bound_cycles_x16'] >= max(4*instructions, feature['loop_recurrence_cycles_x16']),
                f'{expected}: inconsistent instruction classes or latency bound')
        require(set(feature) == set(STATIC + LATENCY + ('bpred', 'cbr_executed', 'mispredicts'))
                and feature['bpred'] is False and all(uint(feature[k]) and feature[k] == 0 for k in
                ('cbr_executed', 'mispredicts')), f'{expected}: dynamic features active')
        prod = row['metered']['production']
        require(prod['complete'] is True and prod['valid'] is True and prod['cpuid_profile'] == 'x86-64-v2'
                and prod['quality'] == 'uninterrupted' and all(uint(prod[k]) and prod[k] == 1 for k in
                ('process_count', 'image_count', 'threads')) and all(uint(prod[k]) and prod[k] == 0 for k in
                ('signals_observed', 'unsupported_rep_entries')), f'{expected}: invalid ordinary receipt')
        require(all(uint(prod[k]) for k in ('rep_bytes', 'rep_movs_bytes', 'rep_stos_bytes'))
                and prod['rep_bytes'] == prod['rep_movs_bytes'] + prod['rep_stos_bytes'],
                f'{expected}: inconsistent REP breakdown')
        require(uint(prod['instructions'], 1) and prod['instructions'] == instructions,
                f'{expected}: ordinary and research instruction counters disagree')
        groups[key].append(row)
    return groups


def pair_bound(slower, faster, names):
    min_slow = min(slower['native'])
    max_fast = max(faster['native'])
    spans = {}
    ratios = []
    unbounded = []
    for name in names:
        a = [row[name] for row in slower['features']]
        b = [row[name] for row in faster['features']]
        spans[name] = {'slow': [min(a), max(a)], 'fast': [min(b), max(b)]}
        if min(b) > 0:
            ratios.append(Fraction(max(a), min(b)))
        elif max(a) > 0:
            unbounded.append(name)
    result = {'min_slow_native_ns': min_slow, 'max_fast_native_ns': max_fast,
              'min_native_ratio': str(Fraction(min_slow, max_fast)), 'feature_ranges': spans,
              'unbounded_features': unbounded, 'max_counter_ratio': None, 'error_lower_bound_ppm_floor': 0}
    if unbounded:
        return result
    # All a_i <= r b_i imply the same inequality for every nonnegative linear combination.
    ratio = max(ratios, default=Fraction(0))
    bound = max(Fraction(0), (min_slow - ratio*max_fast) / (min_slow + ratio*max_fast))
    result['max_counter_ratio'] = str(ratio)
    result['error_lower_bound_ppm_floor'] = int(bound * PPM)
    return result


def report(data, protocol):
    groups = collect_cases(data, protocol)
    cases = {}
    limits = protocol['thresholds']
    detail = {}
    for (program, scale), samples in groups.items():
        name = f'{program}/{scale}'
        native = [row[phase]['cpu_time_ns'] for row in samples for phase in ('native_before', 'native_after')]
        observed = [row['metered']['features'] for row in samples]
        probe_ns = sum(row['metered']['cpu_time_ns'] for row in samples)
        middle, spread = center(native), relative_spread(native)
        detail[name] = {'native': native, 'middle': middle, 'features': observed, 'probe_ns': probe_ns}
        ranges = {}
        for k in STATIC + LATENCY:
            counts = [f[k] for f in observed]
            variation = relative_spread(counts)
            ranges[k] = {'min': min(counts), 'median': median(counts), 'max': max(counts),
                         'spread_ppm': None if variation is None else ppm(variation)}
        meets_floor = min(native) >= limits['min_native_ns']
        meets_spread = spread*PPM <= limits['max_native_spread_ppm']
        cases[name] = {'split': 'train' if program in protocol['train'] else 'validation',
                       'native_min_ns': min(native), 'native_median_ns': float(middle), 'native_max_ns': max(native),
                       'native_spread_ppm': ppm(spread),
                       'native_meets_floor': meets_floor, 'native_meets_spread_limit': meets_spread,
                       'native_stable': meets_floor and meets_spread,
                       'probe_to_native_ratio': round(float(Fraction(2*probe_ns, sum(native))), 6),
                       'counters': ranges}
    heldout = [name for name in cases if cases[name]['split'] == 'validation']
    training = [({k: v['median'] for k, v in case['counters'].items()}, detail[name]['middle'])
                for name, case in cases.items() if case['split'] == 'train']
    models = {}
    for model, columns in protocol['models'].items():
        weights, rank = fit(training, columns)
        scores = {}
        worst_errors = {}
        for name, case in detail.items():
            predicted = [sum(row[k]*w for k, w in weights.items()) for row in case['features']]
            errors = [(prediction - case['middle']) / case['middle'] for prediction in predicted]
            worst = max(errors, key=abs)
            worst_errors[name] = worst
            scores[name] = {'predicted_ns': [round(x) for x in predicted], 'signed_error_ppm': [ppm(x) for x in errors],
                            'worst_signed_error_ppm': ppm(worst),
                            'pass': abs(worst)*PPM <= limits['max_error_ppm']}

        def summary(names):
            if not names:
                return {'cases': 0, 'passed': 0, 'median_case_worst_abs_error_ppm': None,
                        'worst_case': None, 'worst_signed_error_ppm': None}
            worst = max(names, key=lambda k: abs(worst_errors[k]))
            return {'cases': len(names), 'passed': sum(scores[k]['pass'] for k in names),
                    'median_case_worst_abs_error_ppm': ppm(median([abs(worst_errors[k]) for k in names])),
                    'worst_case': worst, 'worst_signed_error_ppm': ppm(worst_errors[worst])}

        models[model] = {'training_matrix_rank': rank, 'coefficients_ns': {k: str(v) for k, v in weights.items()},
                         'all_heldout': summary(heldout),
                         'native_stable_heldout': summary([n for n in heldout if cases[n]['native_stable']]),
                         'cases': scores}
    pairing = protocol['pair']
    slow, fast = ('/'.join(map(str, pairing[key])) for key in ('slow', 'fast'))
    probe_ns = sum(x['probe_ns'] for x in detail.values())
    bracket_ns = sum(sum(x['native']) for x in detail.values())
    return {'schema': 1, 'triplets': len(data['samples']), 'native_stable_cases': sum(x['native_stable'] for x in cases.values()),
            'controls_meeting_duration_floor': sum(t >= limits['min_native_ns'] for c in detail.values() for t in c['native']),
            'probe_cpu_ns': probe_ns, 'sum_native_bracket_ns': bracket_ns,
            'probe_to_native_ratio': round(float(Fraction(2*probe_ns, bracket_ns)), 6),
            'cases': cases, 'models': models, 'pair': {'slow': slow, 'fast': fast,
                'instructions': pair_bound(detail[slow], detail[fast], ['instructions']),
                'all_static': pair_bound(detail[slow], detail[fast], STATIC)}}


def validate_corpus(corpus, protocol):
    require(corpus['corpus_id'] == protocol['corpus_id'] and corpus['aonohako_commit'] == protocol['aonohako_commit'],
            'workload source revision mismatch')
    for case in protocol['cases']:
        program = corpus['programs'][case['workload_id']]
        matches = [c for c in program['cases'] if c['scale'] == case['scale']]
        require(len(matches) == 1 and case['source_sha256'] == program['source_sha256'],
                'missing or ambiguous corpus fixture')
        for text_key, digest_key in [('stdin', 'input_sha256'), ('stdout', 'expected_sha256')]:
            entry = matches[0]
            require(hashlib.sha256(entry[text_key].encode()).hexdigest() == entry[digest_key] == case[digest_key],
                    'fixture content does not match preregistered digest')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dataset', type=Path, help='collected JSON dataset (plain or gzip)')
    parser.add_argument('--protocol', type=Path, default=HERE/'protocol.json')
    parser.add_argument('--corpus', type=Path, default=HERE/'corpus.json')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--output', type=Path, help='write a new JSON score')
    group.add_argument('--verify', type=Path, help='compare with the archived JSON score')
    args = parser.parse_args()
    protocol_bytes = args.protocol.read_bytes()
    corpus_bytes = args.corpus.read_bytes()
    dataset_bytes = args.dataset.read_bytes()
    if dataset_bytes[:2] == b'\x1f\x8b':
        dataset_bytes = gzip.decompress(dataset_bytes)
    data, protocol, corpus = map(json.loads, [dataset_bytes, protocol_bytes, corpus_bytes])
    validate_corpus(corpus, protocol)
    require(data['corpus_sha256'] == hashlib.sha256(corpus_bytes).hexdigest(), 'dataset corpus digest mismatch')
    require(data['protocol_sha256'] == hashlib.sha256(protocol_bytes).hexdigest(), 'dataset protocol digest mismatch')
    require(data['collector_sha256'] == hashlib.sha256((HERE/'collect.py').read_bytes()).hexdigest(),
            'collector source differs from the one used to acquire the dataset')
    require(data['compiler']['version'] == corpus['compiler_version']
            and data['compiler']['flags'] == corpus['compiler_flags'], 'compiler identity mismatch')
    result = report(data, protocol)
    result['dataset_sha256'] = hashlib.sha256(dataset_bytes).hexdigest()
    result['protocol_sha256'] = hashlib.sha256(protocol_bytes).hexdigest()
    result['corpus_sha256'] = data['corpus_sha256']
    if args.verify:
        require(json.loads(args.verify.read_bytes()) == result, 'archived score differs from replay')
        print(f'Verified {result["triplets"]} triplets and {len(result["models"])} fits.')
    elif args.output:
        with args.output.open('x') as stream:
            json.dump(result, stream, indent=2, sort_keys=True)
            stream.write('\n')
    else:
        print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
