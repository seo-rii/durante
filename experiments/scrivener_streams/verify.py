"""Replay stream-locality forecasts and evaluate the six new orders independently of the control."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages import verify as pages
from scrivener_streams.bench import STUDY


def verify(root):
    return pages.verify(root, STUDY, STUDY.scoring.qualification)


if __name__ == "__main__":
    pages.main(verify)
