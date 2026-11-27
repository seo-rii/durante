"""Collect Scrivener stream-locality forecasts before timing the control and held-out inputs."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages import bench
from scrivener_streams import corpus, score

STUDY = bench.Study(HERE, corpus, score, ("accesses", "misses", "disruptive", "position_bits"), ("lanes32",))


if __name__ == "__main__":
    bench.main(STUDY)
