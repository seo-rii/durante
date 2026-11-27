"""Build the lazy deadline guest with the registered stream-locality scanner."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages.prepare import main


if __name__ == "__main__":
    main(HERE, scanner_source=HERE.parent / "scrivener_streams/scan.cpp")
