"""Compare one exact native observation with the median of the next three observations."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deadline_accuracy.bench import main

if __name__ == "__main__":
    main(strategy="warm-three")
