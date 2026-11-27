"""Profile overlapping IOI Wall ranges and record a separate leaf-page input probe."""
import argparse
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
from contest_branch import bench as branch
from cpp20_locality.verify import require

PROFILE_ARMS = (*branch.PROFILE_ARMS, "leaf_pages")


def schedule(plan):
    return branch.schedule(plan, PROFILE_ARMS)


def read_probe(path):
    match = re.fullmatch(rb"([1-9][0-9]*) ([1-9][0-9]*)\n", path.read_bytes())
    require(match is not None, "invalid leaf-page counts")
    accesses, misses = map(int, match.groups())
    require(misses <= accesses, "leaf-page misses exceed accesses")
    return dict(accesses=accesses, misses=misses)


def collect_run(payload, out):
    from wall_locality import corpus, score
    branch.collect_study(payload, out, HERE, corpus.make, score.predict, profile_arms=PROFILE_ARMS, read_probe=read_probe)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--payload", required=True, type=Path)
    cli.add_argument("--out", required=True, type=Path)
    args = cli.parse_args()
    collect_run(args.payload.resolve(), args.out.resolve())
