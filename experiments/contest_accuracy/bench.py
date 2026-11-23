"""Run the registered contest inputs through the same exact-one experiment as the original confirmation."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deadline_accuracy.bench import main

if __name__ == "__main__":
    main(exact=True, contest=True)
