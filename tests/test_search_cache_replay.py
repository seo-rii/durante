"""Check the post-hoc cache controls, case identities and measured reference times."""
from pathlib import Path
import subprocess
import sys
import unittest


class SearchCacheReplay(unittest.TestCase):
    def test_hand_controls_and_archived_trace_counts(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run([sys.executable, root / "experiments/search_cache_replay/check.py", "--quick"],
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
