"""Reconcile the warm-clock study with its registered inputs and independent native observations."""
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy.verify import verify
from shape_calibration.verify_combined import unpack

PLAN = ROOT / "experiments/contest_warm/plan.json"
RESULTS = ROOT / "experiments/results/gce-contest-warm"


def read(path):
    return json.loads(path.read_bytes())


class WarmContestReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(temporary.cleanup)
        cls.output = unpack(RESULTS / "output.tar.xz", Path(temporary.name))
        cls.summary = read(cls.output / "summary.json")
        cls.plan = read(PLAN)

    def test_only_controls_share_earlier_elf_and_input(self):
        def identity(case):
            return case["binary_sha256"], case["stdin_sha256"]
        previous = {identity(case) for case in
                    read(ROOT / "experiments/contest_accuracy/plan.json")["cases"]}
        cases = self.plan["cases"]
        self.assertTrue(all(c["split"] == "holdout" for c in cases))
        self.assertEqual(len(set(map(identity, cases))), 14)
        self.assertEqual({c["case_id"] for c in cases if identity(c) in previous},
                         {"wall/crossed_intervals", "money/dense_scrambled"})
        for case in cases:
            self.assertEqual(identity(case) in previous, case["cohort"] == "previous")
        self.assertEqual(Counter(c["program"] for c in cases if c["cohort"] == "prospective"),
                         {"dreaming": 4, "posterize": 4, "scrivener": 4})

    def test_published_scores_match_native_votes_and_fresh_live_results(self):
        result = verify(self.output, "warm-three")
        self.assertEqual(result, read(RESULTS / "metrics.json"))
        self.assertEqual(result["seeds"], {})
        self.assertEqual(result["totals"]["prospective"]["inputs"], 12)
        self.assertEqual(result["totals"]["previous"]["inputs"], 2)
        samples = defaultdict(list)
        for row in self.summary["native"]:
            samples[row["case_id"]].append(1000 * (row["user_us"] + row["sys_us"]))
        refs = {row["pid"] for row in self.summary["native"]}
        self.assertEqual(len(refs), 154)
        self.assertEqual(result["native_runs"], 154)
        pids = []
        classes = defaultdict(Counter)
        majority = Counter()
        for case in self.plan["cases"]:
            name = case["case_id"]
            folder = self.output / "cases" / name
            trial = result["cases"][name]
            timings = samples[name]
            self.assertEqual(len(timings), 11)
            self.assertEqual(trial["native_ns"], timings)
            packet = folder / "deadline-packet"
            policies = [read(packet / f"policy-{i}.json") for i in range(4)]
            self.assertEqual((packet / "policy-0.json").read_bytes(), (folder / "policy.json").read_bytes())
            cpus = [1000 * (p["native"]["user_us"] + p["native"]["sys_us"]) for p in policies]
            self.assertEqual(trial["extra_ns"], cpus[1:])
            self.assertEqual(trial["baseline_ns"], cpus[0])
            selected = sorted(range(1, 4), key=lambda i: (cpus[i], i))[1]
            self.assertEqual(read(packet / "packet.json")["selected_index"], selected)
            self.assertEqual(trial["packet_warm_ns"], cpus[selected])
            pids.extend(p["native"]["pid"] for p in policies)
            fixed = set(case["fixed_limits_ms"])
            adaptive = {max(1, (cpus[0] * pct + 99_999_999) // 100_000_000) for pct in self.plan["deadline_percent"]}
            self.assertEqual([p["limit_ms"] for p in trial["deadlines"]], sorted(fixed | adaptive))
            for i, point in enumerate(trial["deadlines"]):
                ms = point["limit_ms"]
                self.assertEqual((point["fixed"], bool(point["adaptive_percent"])), (ms in fixed, ms in adaptive))
                votes = sum(t > ms * 1_000_000 for t in timings)
                self.assertEqual(point["native_tle_reps"], votes)
                for arm, estimate in (("baseline", cpus[0]), ("warm", cpus[selected])):
                    actual = read(folder / "limits" / arm / f"{i:02d}" / "result.json")
                    killed = actual["status"] == "budget_exceeded"
                    self.assertIn(actual["status"], ("within_budget", "budget_exceeded"))
                    self.assertEqual((point[arm]["pid"], point[arm]["tle"], point[arm]["preflight_ns"]),
                                     (actual["pid"], killed, estimate))
                    pids.append(actual["pid"])
                    if 0 < votes < 11:
                        classification = "mixed_native"
                    elif killed == (votes == 11):
                        classification = "agreement"
                    else:
                        classification = "false_tle" if killed else "false_accept"
                    classes[arm][classification] += 1
                    majority[arm] += (killed == (votes >= 6))
        self.assertEqual(len(set(pids)), len(pids))
        self.assertTrue(refs.isdisjoint(pids))
        totals = result["totals"]["all"]
        self.assertEqual((totals["baseline_paid_runs"], totals["warm_extra_runs"]), (14, 42))
        for arm in ("baseline", "warm"):
            tally = totals["deadlines"]["all"][arm]
            for label in ("agreement", "false_tle", "false_accept", "mixed_native"):
                self.assertEqual(tally[label], classes[arm][label])
            self.assertEqual(tally["majority_matches"], majority[arm])

    def test_fresh_guest_repeats_all_registered_identities_on_a_distinct_boot(self):
        repeat = RESULTS.with_name("gce-contest-warm-repeat")
        with tempfile.TemporaryDirectory() as tmp:
            second = unpack(repeat / "output.tar.xz", Path(tmp))
            result = verify(second, "warm-three")
            self.assertEqual(result, read(repeat / "metrics.json"))
            self.assertEqual(result["native_runs"], 154)
            first_summary = self.summary
            second_summary = read(second / "summary.json")
            for name in ("plan_sha256", "rules_sha256", "probe_sha256"):
                self.assertEqual(first_summary[name], second_summary[name])
            def identities(root):
                observed = {}
                boots = set()
                for case in self.plan["cases"]:
                    name = case["case_id"]
                    paid = read(root / "cases" / name / "policy.json")
                    native, capture = paid["native"], paid["anchor"]
                    observed[name] = (capture["elf_sha256"], capture["stdin_sha256"], native["stdout_sha256"])
                    boots.add(native["boot_sha256"])
                self.assertEqual(len(boots), 1)
                return observed, boots.pop()
            expected = {case["case_id"]: (case["binary_sha256"], case["stdin_sha256"], case["stdout_sha256"])
                        for case in self.plan["cases"]}
            first_ids, first_boot = identities(self.output)
            second_ids, second_boot = identities(second)
            self.assertEqual(first_ids, expected)
            self.assertEqual(second_ids, expected)
            self.assertNotEqual(first_boot, second_boot)

    def test_warmup_cannot_be_selected_as_the_median(self):
        first = self.summary["rows"][0]["case_id"]
        manifest = self.output / "cases" / first / "deadline-packet/packet.json"
        original = manifest.read_bytes()
        self.addCleanup(manifest.write_bytes, original)
        changed = json.loads(original)
        changed["selected_index"] = 0
        manifest.write_text(json.dumps(changed))
        with self.assertRaisesRegex(ValueError, "invalid warm-median packet"):
            verify(self.output, "warm-three")

    def check_swapped_native_order(self, first_pid, second_pid):
        trace = self.output / "exec-trace"
        original = trace.read_bytes()
        self.addCleanup(trace.write_bytes, original)
        lines = original.splitlines(keepends=True)
        def index(pid):
            idx, = (i for i, line in enumerate(lines) if f" pid={pid} ".encode() in line)
            return idx
        a, b = index(first_pid), index(second_pid)
        self.assertLess(a, b)
        lines[a], lines[b] = lines[b], lines[a]
        trace.write_bytes(b"".join(lines))
        with self.assertRaisesRegex(ValueError, "exec trace order differs"):
            verify(self.output, "warm-three")

    def test_warm_measurements_must_keep_their_recorded_order(self):
        folder = self.output / "cases" / self.summary["rows"][0]["case_id"] / "deadline-packet"
        self.check_swapped_native_order(read(folder / "policy-0.json")["native"]["pid"],
                                        read(folder / "policy-3.json")["native"]["pid"])

    def test_independent_references_cannot_enter_the_warm_packet(self):
        last = self.summary["rows"][-1]["case_id"]
        final_policy = self.output / "cases" / last / "deadline-packet/policy-3.json"
        self.check_swapped_native_order(read(final_policy)["native"]["pid"], self.summary["native"][0]["pid"])
