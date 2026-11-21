import copy
import json
import unittest

from score_holdout import HERE, LATENCY, RECURRENCE, STATIC, evaluate


def synthetic(plan, fixtures):
    samples = []
    programs = {n: {'source_sha256': f['source_sha256'], 'binary_sha256': str(i)*64}
                for i, (n, f) in enumerate(fixtures['programs'].items(), 1)}
    pid = 2
    for rep, offset in enumerate(plan['round_rotations'], 1):
        for name in plan['programs'][offset:] + plan['programs'][:offset]:
            fixture = fixtures['programs'][name]['cases'][0]
            modes = ['full', 'fast'] if len(samples) in plan['full_first_indices'] else ['fast', 'full']
            phases = ['native_before', *modes, 'native_after']
            row = {'workload_id': name, 'scale': 1, 'repetition': rep, 'phase_order': phases,
                   'input_sha256': fixture['input_sha256'], 'expected_sha256': fixture['expected_sha256'], **programs[name]}
            for mode in phases:
                pid += 1
                ns = {'full': 600_000_000, 'fast': 300_000_000}.get(mode, 100_000_000)
                obs = {'pid': pid, 'user_ns': ns, 'system_ns': 0, 'cpu_time_ns': ns, 'wall_ns': ns + 30,
                       'exit_code': 0, 'stdout_sha256': row['expected_sha256']}
                if mode in modes:
                    obs['production'] = {'instructions': 1_250_000_000, 'threads': 1, 'image_count': 1,
                                         'process_count': 1, 'signals_observed': 0, 'cpuid_profile': 'x86-64-v2',
                                         'complete': True, 'valid': True, 'quality': 'uninterrupted',
                                         'unsupported_rep_entries': 0, 'rep_bytes': 0, 'rep_movs_bytes': 0,
                                         'rep_stos_bytes': 0}
                    if mode == 'full':
                        obs['features'] = dict.fromkeys(STATIC + LATENCY, 0)
                        obs['features'].update(instructions=1_250_000_000, bpred=False, cbr_executed=0,
                                               mispredicts=0, bound_cycles_x16=5_000_000_000)
                    else:
                        obs['features'] = {'version': 1, RECURRENCE: 0}
                row[mode] = obs
            samples.append(row)
    return {'schema': 1, 'complete': True, 'collector_pid': 2, 'cpu': 1, 'tools': plan['tools'],
            'programs': programs, 'samples': samples}


class HoldoutTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = json.loads((HERE/'protocol.json').read_bytes())
        cls.fixtures = json.loads((HERE/'fixtures.json').read_bytes())
        cls.base = synthetic(cls.plan, cls.fixtures)

    def score(self, data):
        return evaluate(data, self.plan, self.fixtures)

    def test_frozen_clock_and_counterbalanced_orders(self):
        s = self.score(self.base)
        self.assertEqual(s['full']['first_in_rounds'], 5)
        self.assertEqual(s['fast']['first_in_rounds'], 4)
        self.assertTrue(s['all_probes_agree'] and s['full']['all_cases_pass'] and s['fast']['all_cases_pass'])
        self.assertEqual(s['pooled_fast_to_full_probe_cpu'], .5)
        for case in s['cases'].values():
            self.assertEqual({r['first_probe'] for r in case['rounds']}, {'fast', 'full'})
            self.assertEqual(case['median_paired_fast_to_full_probe_cpu'], .5)

    def test_parity_uses_own_counts_and_exact_full_denominator(self):
        data = copy.deepcopy(self.base)
        obs = data['samples'][0]['fast']
        obs['production']['instructions'] = 1_251_250_000
        self.assertTrue(self.score(data)['all_probes_agree'])  # exactly 0.1%
        obs['production']['instructions'] += 1
        result = self.score(data)
        self.assertFalse(result['all_probes_agree'])
        self.assertTrue(result['fast']['all_cases_pass'])
        first = result['cases']['gcd']['rounds'][0]
        self.assertEqual(first['fast_minus_full_prediction_ppm'], 1000)
        self.assertFalse(first['prediction_agreement_pass'])
        self.assertEqual(first['fast']['instructions'], 1_251_250_001)

    def test_all_three_rounds_against_six_native_controls(self):
        data = copy.deepcopy(self.base)
        obs = data['samples'][0]['fast']
        obs['production']['instructions'] = 1_000_000_000
        self.assertTrue(self.score(data)['fast']['all_cases_pass'])  # exactly -20%
        obs['production']['instructions'] -= 1
        s = self.score(data)
        self.assertFalse(s['fast']['all_cases_pass'])
        self.assertTrue(s['full']['all_cases_pass'])
        self.assertEqual(s['fast']['passed_cases'], 2)
        row = data['samples'][0]['native_before']
        row['user_ns'] = row['cpu_time_ns'] = 10_000_000
        result = self.score(data)
        self.assertEqual(result['cases']['gcd']['native_median_ns'], 100_000_000)
        self.assertFalse(result['cases']['gcd']['native_meets_floor'])
        self.assertFalse(result['cases']['gcd']['native_meets_spread_limit'])
        self.assertEqual(result['fast']['passed_cases'], 2)

    def test_reject_changed_receipts_and_order(self):
        for path, bad in [(('phase_order',), ['native_before', 'fast', 'full', 'native_after']),
                          (('full', 'features', 'instructions'), 1), (('fast', 'features', 'version'), True),
                          (('fast', 'production', 'cpuid_profile'), ''), (('full', 'user_ns'), 123),
                          (('native_before', 'stdout_sha256'), '0'*64), (('binary_sha256',), '0'*64)]:
            data = copy.deepcopy(self.base)
            node = data['samples'][0]
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = bad
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.score(data)
        for action in ['omit', 'exchange']:
            data = copy.deepcopy(self.base)
            if action == 'omit':
                data['samples'].pop()
            else:
                data['samples'][2], data['samples'][3] = data['samples'][3], data['samples'][2]
            with self.subTest(action=action), self.assertRaises(ValueError):
                self.score(data)


if __name__ == '__main__':
    unittest.main()
