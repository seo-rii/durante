"""Seal Scrivener clock forecasts before timing the regression controls and fresh inputs."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages import bench
from scrivener_rank import corpus, score
from scrivener_streams.bench import STUDY as STREAM_STUDY

SCAN_FIELDS = (*STREAM_STUDY.scan_fields, "displacement", "stale_disruptive")
STUDY = bench.Study(HERE, corpus, score, SCAN_FIELDS, corpus.CONTROLS)


if __name__ == "__main__":
    bench.main(STUDY)
