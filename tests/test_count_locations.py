"""Location diagnostics require complete, identity-bound counter evidence."""
import copy
import unittest

from count_locations import ModuleUnloaded, compare_profiles, parse_profile


def profile(hits=2, module_name="linux-vdso.so.1", build_id="aa", start=4096, unloads=0):
    return ("PROFILE\t3\t65536\t128\t1048320\n"
            f"B\t0\t10\t3\t{hits}\n"
            f"M\t0\t{build_id}\t{start:x}\t{start+4096:x}\t{module_name}\n"
            f"END\t1\t1\t1\t{3*hits}\t0\t0\t1\t1\t{unloads}\n").encode()


class CountLocationTests(unittest.TestCase):
    def test_complete_histogram_reconciles_direct_charges(self):
        parsed = parse_profile(profile(), 6)
        self.assertEqual(parsed["weighted_charge"], 6)
        self.assertEqual(parsed["blocks"], {("aa", 16, 3): 2})
        for total in (5, 7):
            with self.assertRaises(ValueError):
                parse_profile(profile(), total)

    def test_module_identity_uses_build_id_and_offset_not_load_address(self):
        observations = [parse_profile(profile(start=start), 6) for start in (4096, 8192, 16384)]
        self.assertEqual(compare_profiles(observations)["varying_blocks"], [])
        observations[-1] = parse_profile(profile(build_id="bb"), 6)
        self.assertEqual(len(compare_profiles(observations)["varying_blocks"]), 2)

    def test_unload_prevents_attribution_to_a_replacement_at_the_same_address(self):
        self.assertEqual(parse_profile(profile(), 6)["module_unloads"], 0)
        for identity in ("aa", "bb"):
            with self.subTest(identity=identity), self.assertRaises(ModuleUnloaded) as error:
                parse_profile(profile(build_id=identity, unloads=1), 6)
            self.assertEqual(error.exception.count, 1)
        for unloads in (-1, 2**64):
            with self.subTest(unloads=unloads), self.assertRaisesRegex(ValueError, "invalid module unload"):
                parse_profile(profile(unloads=unloads), 6)

    def test_clock_module_variation_is_visible_without_any_allowance(self):
        observations = [parse_profile(profile(hits=count), count*3) for count in (2, 3, 2)]
        result = compare_profiles(observations)
        self.assertEqual(result["totals"], [6, 9, 6])
        self.assertEqual(result["varying_blocks"], [{"build_id": "aa", "module": "linux-vdso.so.1",
            "offset": "0x10", "instructions_per_entry": 3, "hits": [2, 3, 2],
            "charged_instructions": [6, 9, 6]}])
        with self.assertRaises(ValueError):
            compare_profiles(observations[:2])

    def test_missing_or_changed_block_lengths_cannot_be_cancelled_by_a_total(self):
        first = parse_profile(profile(), 6)
        second = parse_profile(profile().replace(b"10\t3\t2", b"10\t2\t3"), 6)
        result = compare_profiles([first, second, copy.deepcopy(first)])
        self.assertEqual(result["totals"], [6, 6, 6])
        self.assertEqual(len(result["varying_blocks"]), 2)

    def test_incomplete_capture_or_replay_cannot_produce_accepted_locations(self):
        baseline = profile()
        for label, changed in (
                ("truncated", baseline.rstrip(b"\n")),
                ("incomplete", baseline.replace(b"END\t1", b"END\t0")),
                ("different bounds", baseline.replace(b"65536", b"32768")),
                ("missing entry", baseline.replace(b"B\t0\t10\t3\t2\n", b"")),
                ("rollback", baseline.replace(b"6\t0\t0\t1\t1", b"7\t1\t1\t1\t1")),
                ("replay", baseline.replace(b"6\t0\t0\t1\t1", b"6\t0\t1\t1\t1")),
                ("extra thread", baseline.replace(b"6\t0\t0\t1\t1", b"6\t0\t0\t2\t2")),
                ("size", b"x" * 1048577)):
            with self.subTest(label=label), self.assertRaises(ValueError):
                parse_profile(changed, 6)

    def test_duplicate_or_disconnected_locations_fail(self):
        baseline = profile()
        for changed in (
                baseline.replace(b"B\t0\t10\t3\t2\n", b"B\t0\t10\t3\t2\n" * 2),
                baseline.replace(b"B\t0", b"B\t1"),
                baseline.replace(b"B\t0\t10", b"B\t0\t1000"),
                baseline.replace(b"M\t0\taa", b"M\t0\tunavailable"),
                baseline.replace(b"END\t", b"unexpected\nEND\t")):
            with self.subTest(data=changed), self.assertRaises(ValueError):
                parse_profile(changed, 6)
