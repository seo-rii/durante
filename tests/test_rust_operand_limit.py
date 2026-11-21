from fractions import Fraction
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("model_limit", ROOT / "experiments/rust_operand_bytes/model_limit.py")
model_limit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(model_limit)


def case(latency, memory, native=(1, 1, 1)):
    def stats(value):
        if isinstance(value, int):
            value = (value,) * 3
        return dict(zip(("min", "median", "max"), value, strict=True))
    return {"features": {"latency_cycles": stats(latency), "memory_bytes": stats(memory)},
            **{f"native_{stat}_ns": value for stat, value in stats(native).items()}}


class RetrospectiveModelLimitTests(unittest.TestCase):
    def test_interior_optimum_reports_both_limiting_signs(self):
        rows = {"flat": case(1, 1), "compute": case(5, 1), "stream": case(1, 3)}
        result = model_limit.minimum_worst_error(rows, False)
        self.assertEqual(result["worst_error_fraction"], "2/5")
        self.assertEqual(result["coefficients_ns"], {"latency_cycles": "1/5", "memory_bytes": "2/5"})
        self.assertEqual(result["limiting_overpredictions"], ["compute", "stream"])
        self.assertEqual(result["limiting_underpredictions"], ["flat"])

    def test_each_axis_can_be_the_only_exact_solution(self):
        for axis in ("latency_cycles", "memory_bytes"):
            rows = {"a": case(1, 1), "b": case(1, 3) if axis == "latency_cycles" else case(3, 1)}
            result = model_limit.minimum_worst_error(rows, False)
            self.assertEqual(result["worst_error_ppm_floor"], 0)
            self.assertEqual(result["coefficients_ns"][axis], "1")
            self.assertEqual(set(result["coefficients_ns"].values()), {"0", "1"})

    def test_independent_ranges_can_lower_but_cannot_erase_all_disagreement(self):
        rows = {"one": case(1, 1),
                "three": case((2, 3, 4), (2, 3, 4), (1, 1, Fraction(3, 2)))}
        self.assertEqual(model_limit.minimum_worst_error(rows, False)["worst_error_fraction"], "1/2")
        result = model_limit.minimum_worst_error(rows, True)
        self.assertEqual(result["worst_error_fraction"], "1/7")
        self.assertEqual(result["worst_error_ppm_floor"], 142_857)
        self.assertEqual(result["limiting_overpredictions"], ["three"])
        self.assertEqual(result["limiting_underpredictions"], ["one"])

    def test_zero_rates_and_overlapping_intervals(self):
        rows = {"zero": case(0, 0)}
        result = model_limit.minimum_worst_error(rows, False)
        self.assertEqual(result["worst_error_fraction"], "1")
        self.assertEqual(result["limiting_underpredictions"], ["zero"])
        rows = {"compute": case((0, 0, 1), 0), "stream": case(0, (0, 0, 1))}
        result = model_limit.minimum_worst_error(rows, True)
        self.assertEqual(result["worst_error_fraction"], "0")


if __name__ == "__main__":
    unittest.main()
