"""Build the verified Wall workload, Durante client and standalone leaf-page counter."""
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_branch.prepare import main

if __name__ == "__main__":
    main(HERE)
