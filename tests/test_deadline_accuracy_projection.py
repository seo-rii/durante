from pathlib import Path
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "experiments"))
from deadline_accuracy.replay_aonohako import project_limits
from deadline_accuracy import replay_aonohako
from shape_calibration.aonohako_clock import ProjectionUnavailable


class PartialProjection(unittest.TestCase):
    def test_group_failure_tries_individual_limits_and_retains_unavailable(self):
        cert = {"limits_ms": [1]}
        export = Mock(side_effect=[ProjectionUnavailable(), cert, ProjectionUnavailable()])
        result = project_limits(export, [1, 2])
        self.assertEqual(result, [([1], cert), ([2], None)])
        self.assertEqual([args.args[0] for args in export.call_args_list], [[1, 2], [1], [2]])

    def test_invalid_evidence_is_fatal_even_when_only_one_limit_fails(self):
        export = Mock(side_effect=[ProjectionUnavailable(), ValueError("bad capture")])
        with self.assertRaisesRegex(ValueError, "bad capture"):
            project_limits(export, [5, 7])

    def test_single_success_and_single_failure_each_only_try_once(self):
        for answer in (None, {"limits_ms": [151]}):
            export = Mock(side_effect=[answer if answer else ProjectionUnavailable()])
            self.assertEqual(project_limits(export, [151]), [([151], answer)])
            export.assert_called_once_with([151])

    def test_registered_strategy_determines_verified_arm_and_actual_live_receipt(self):
        root = Path("guest/output")
        binaries, go = Path("guest/binaries"), Path("durante-reference")
        primary = dict(cases={"x/a": dict(program="x")}, summary_sha256="abc")
        for strategy, arm in (("exact-one", "exact"), ("near-three", "gate")):
            with self.subTest(strategy=strategy):
                events = [dict(method=name, index=0, limit_ms=17, selection_path=name + ".json",
                               policy_path=name + "-policy.json") for name in ("baseline", arm)]
                summary = dict(rows=[dict(case_id="x/a", limits=events)])
                with patch.object(replay_aonohako.verify, "verify", return_value=primary) as validate, \
                     patch.object(replay_aonohako.verify, "load", return_value=summary), \
                     patch.object(replay_aonohako.aonohako_clock, "export", return_value={"limits_ms":[17]}), \
                     patch.object(replay_aonohako.aonohako_clock, "replay_live",
                                  side_effect=("within_budget", "budget_exceeded")) as live:
                    result = replay_aonohako.replay(root, binaries, go, strategy)
                validate.assert_called_once_with(root, strategy)
                self.assertEqual(result["arms"], {"baseline":{"within_budget":1}, arm:{"budget_exceeded":1}})
                self.assertEqual(result["bindings"], 2)
                self.assertEqual(result["certified_projections"], 2)
                self.assertEqual(result.get("strategy"), "exact-one" if arm == "exact" else None)
                for call, name in zip(live.call_args_list, ("baseline", arm), strict=True):
                    self.assertEqual(call.args[2], root / "cases/x/a/limits" / name / "00/result.json")
