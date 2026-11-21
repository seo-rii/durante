import copy
import hashlib
import json
import unittest

from score import HERE, LATENCY, STATIC, fit, pair_bound, report, validate_corpus


def synthetic(protocol):
    def digest(value):
        return hashlib.sha256(value.encode()).hexdigest()

    dataset = {'schema': 1, 'complete': True, 'cpu': 1, 'collector_pid': 1, 'programs': {}, 'samples': [],
               'tools': {k: digest(k) for k in ('launcher', 'meter', 'runtime', 'probe', 'production_reader')}}
    dataset['tools']['meter'] = protocol['meter_sha256']
    pid = 1
    for rep, offset in enumerate(protocol['round_rotations'], 1):
        for case in protocol['cases'][offset:] + protocol['cases'][:offset]:
            name, scale = case['workload_id'], case['scale']
            compiled = {'binary_sha256': digest(name), 'source_sha256': case['source_sha256']}
            dataset['programs'][name] = compiled
            row = {k: v for k, v in case.items() if k != 'split'}
            row.update(compiled, repetition=rep)
            ins = 100_000_000 * scale
            muls = (ins // 2 if name in ('integer', 'branch') else 0)
            ns = ins + 2*muls
            for phase in ('native_before', 'metered', 'native_after'):
                pid += 1
                row[phase] = {'pid': pid, 'cpu_time_ns': ns, 'user_ns': ns, 'system_ns': 0,
                              'wall_ns': ns + 100, 'exit_code': 0, 'stdout_sha256': case['expected_sha256']}
            feature = {key: 0 for key in STATIC + LATENCY}
            feature.update(instructions=ins, muls=muls, bpred=False, cbr_executed=0, mispredicts=0,
                           loop_recurrence_cycles_x16=16*muls, bound_cycles_x16=max(4*ins, 16*muls))
            row['metered']['features'] = feature
            row['metered']['production'] = {'valid': True, 'complete': True, 'process_count': 1, 'image_count': 1,
                                            'threads': 1, 'cpuid_profile': 'x86-64-v2', 'quality': 'uninterrupted',
                                            'signals_observed': 0, 'unsupported_rep_entries': 0, 'instructions': ins,
                                            'rep_bytes': 0, 'rep_stos_bytes': 0, 'rep_movs_bytes': 0}
            dataset['samples'].append(row)
    return dataset


class ScoreTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((HERE/'protocol.json').read_bytes())
        cls.base = synthetic(cls.plan)

    def test_prespecified_fit_and_no_heldout_leakage(self):
        summary = report(self.base, self.plan)
        self.assertEqual(summary['models']['multiply']['coefficients_ns'], {'instructions': '1', 'muls': '2'})
        self.assertEqual(summary['models']['multiply']['all_heldout']['passed'], 9)
        mutated = copy.deepcopy(self.base)
        for row in mutated['samples']:
            if row['workload_id'] == 'sort' and row['scale'] == 1 and row['repetition'] == 1:
                row['native_before']['user_ns'] *= 2
                row['native_before']['cpu_time_ns'] *= 2
        result = report(mutated, self.plan)
        self.assertEqual(result['models']['multiply']['native_stable_heldout']['cases'], 8)
        self.assertEqual(result['models']['multiply']['all_heldout']['cases'], 9)
        for model, fit_result in result['models'].items():
            self.assertEqual(fit_result['coefficients_ns'], summary['models'][model]['coefficients_ns'])

    def test_exact_boundary_and_all_repetitions_gate(self):
        data = copy.deepcopy(self.base)
        row = next(s for s in data['samples'] if (s['workload_id'], s['scale'], s['repetition']) == ('sort', 1, 2))
        row['metered']['features']['muls'] = 10_000_000
        self.assertTrue(report(data, self.plan)['models']['multiply']['cases']['sort/1']['pass'])
        row['metered']['features']['muls'] += 1
        score = report(data, self.plan)['models']['multiply']['cases']['sort/1']
        self.assertFalse(score['pass'])
        self.assertEqual(score['worst_signed_error_ppm'], 200_000)  # rounding cannot grant a pass

    def test_nnls_ties_and_nonnegative_boundary(self):
        def calculate(rows):
            return fit([({'x': x, 'y': y}, t) for x, y, t in rows], ['x', 'y'])
        self.assertEqual(calculate([(2, 4, 2), (3, 6, 3)]), ({'x': 1, 'y': 0}, 1))
        self.assertEqual(calculate([(2, 0, 2), (3, 0, 3)]), ({'x': 1, 'y': 0}, 1))
        self.assertEqual(calculate([(1, 0, 10), (1, 1, 5)]), ({'x': 6, 'y': 0}, 2))
        self.assertEqual(calculate([(1, 3, 7), (2, 1, 4)]), ({'x': 1, 'y': 2}, 2))

    def test_pair_can_rule_out_fits_or_remain_uninformative(self):
        def group(t, x, y):
            return {'native': [t, t], 'features': [{'x': x, 'y': y}] * 3}
        slow, fast = group(4, 1, 0), group(1, 1, 0)
        self.assertEqual(pair_bound(slow, fast, ['x', 'y'])['error_lower_bound_ppm_floor'], 600_000)
        slow['features'][0]['y'] = 15
        result = pair_bound(slow, fast, ['x', 'y'])
        self.assertEqual(result['unbounded_features'], ['y'])
        self.assertIsNone(result['max_counter_ratio'])
        fast['features'][0]['y'] = 1
        self.assertEqual(pair_bound(slow, fast, ['x', 'y'])['error_lower_bound_ppm_floor'], 0)

    def test_invalid_measurements_bail_out_before_scoring(self):
        for field, bad in [('source_sha256', '0'*64), ('input_sha256', '0'*64), ('binary_sha256', '0'*64),
                           ('scale', True), ('repetition', 2)]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.base)
                data['samples'][0][field] = bad
                with self.assertRaises(ValueError):
                    report(data, self.plan)
        for phase, field, bad in [('native_after', 'stdout_sha256', '0'*64), ('native_before', 'exit_code', 1),
                                  ('native_before', 'user_ns', 0), ('metered', 'pid', 1)]:
            with self.subTest(field=field):
                data = copy.deepcopy(self.base)
                data['samples'][0][phase][field] = bad
                with self.assertRaises(ValueError):
                    report(data, self.plan)
        for key, value in [('instructions', 1), ('cpuid_profile', ''), ('threads', 2), ('rep_bytes', 1)]:
            with self.subTest(key=key):
                data = copy.deepcopy(self.base)
                data['samples'][0]['metered']['production'][key] = value
                with self.assertRaises(ValueError):
                    report(data, self.plan)
        for mode in ('omitted', 'duplicated', 'exchanged'):
            data = copy.deepcopy(self.base)
            rows = data['samples']
            if mode == 'omitted':
                rows.pop()
            elif mode == 'duplicated':
                rows[44] = rows[43]
            else:
                rows[14], rows[15] = rows[15], rows[14]
            with self.subTest(mode=mode), self.assertRaises(ValueError):
                report(data, self.plan)

    def test_distinct_pids_may_decrease_between_serial_launches(self):
        data = copy.deepcopy(self.base)
        data['samples'][0]['native_before']['pid'] = 65536
        self.assertEqual(report(data, self.plan)['triplets'], 45)

    def test_corpus_bytes_against_frozen_protocol(self):
        corpus = json.loads((HERE/'corpus.json').read_bytes())
        validate_corpus(corpus, self.plan)
        corpus['programs']['sort']['cases'][0]['stdout'] += '\n'
        with self.assertRaises(ValueError):
            validate_corpus(corpus, self.plan)


if __name__ == '__main__':
    unittest.main()
