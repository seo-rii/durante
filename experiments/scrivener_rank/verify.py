"""Replay Scrivener CPU-1 profiles, reconstruct sealed forecasts and evaluate deadlines."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages import verify as pages
from scrivener_rank.bench import STUDY


def verify(root):
    return pages.verify(root, STUDY, STUDY.scoring.qualification)


if __name__ == "__main__":
    pages.main(verify)
