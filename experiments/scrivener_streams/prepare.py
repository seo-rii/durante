"""Build the registered Scrivener stream-locality guest from a v12 worker installation."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from scrivener_pages.prepare import main


if __name__ == "__main__":
    main(HERE)
