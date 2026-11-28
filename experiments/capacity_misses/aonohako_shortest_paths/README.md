# A reference-anchored ML4 clock on Aonohako's 65 ms shortest-paths input

The frozen Dijkstra ML4 coefficients correctly classified all six evaluated
submissions at the original 65 ms deadline on each of two new Milan boots.
Aonohako's original instruction/REP formula classified four. Both clocks
rejected the two genuinely slow solutions. These are simulated deadlines from
completed CPU and counter measurements. The [original Aonohako experiment](https://github.com/seo-rii/aonohako/pull/178)
exercised live v13 cutoffs.

| Original ELF | Native CPU on both boots (ms) | Original cheap clock (ms) | Anchored ML4, all sample pairs (ms) | Native / ML4 |
| --- | ---: | ---: | ---: | --- |
| Priority-queue reference, used for scaling | 14.37–15.79 | 15.17 | 14.37–15.79 | Pass / pass |
| Fast bucket queue | 10.76–12.73 | 11.81 | 12.50–13.74 | Pass / pass |
| Block queue, size 8 | 48.86–50.96 | 99.36 | 56.60–62.19 | Pass / pass |
| Block queue, size 2048 | 29.43–32.79 | 80.03 | 46.82–51.44 | Pass / pass |
| Block queue, size 4 | 87.20–90.75 | 186.27 | 100.60–110.54 | Timeout / timeout |
| Original linear-scan implementation | 698.15–722.10 | 1404.18 | 714.90–785.48 | Timeout / timeout |
| Ordered set | 21.92–23.61 | 18.71 | 17.49–19.22 | Pass / pass |

For each submission, the simulation multiplies its ML4 score by the original
reference ELF's measured native CPU and divides by that reference's ML4 score.
The table permits every combination of those observations across both guests.
Only the reference supplies the scale. A secondary control normalizes the
cheap clock to the same reference and still predicts timeouts for block sizes
8 and 2048. This additional control was outside the registered comparison.
[Protocol](protocol.json) · [Full intervals, costs and archived live verdicts](results.json)

The diagnostic's median CPU cost for block size 2048 was about 274× native on
both boots. Its lowest ML4 estimate for this case was over 40% above the
highest native CPU observation. Correct verdicts at 65 ms therefore coexist
with substantial timing error. Both problematic submissions were already
known when the panel was selected. Additional challenges are necessary before
extending the conclusion to other deadlines, inputs or contestants.

## Measurements and reproduction

The [registration](registration.json) and [frozen plan](frozen.json) were
published in [ac69db1](https://github.com/seo-rii/durante/commit/ac69db192fec95a4cbad88184db0c657f4c0bfec)
before either VM was created. The seven Aonohako ELFs, seeded input and
expected output match their original hashes. Each guest ran all seven
programs seven times natively and twice with the corrected cache probe, on
CPU 1 of an SMT-disabled N2D AMD Milan VM. Each measured process produced
the checksum for `985955\n`. The 98 native and 28 diagnostic executions
were direct process runs with measured user plus system CPU. All four counter readings matched for each
of the seven executables. Both pinned cores recorded zero steal ticks. The native
binary used the host CPU profile. The diagnostic exposed x86-64-v2. The
original HTTP judge outcomes are recorded separately. [VM and disk deletion records](evidence/cloud.json)

Run from this directory with Python 3.11 and NumPy:

```sh
python3 -B -m unittest discover
python3 -B replay.py --verify results.json
python3 -B replay.py --verify results.json --aonohako "$AONOHAKO_CHECKOUT"
```

The final command additionally reads the original public v13 receipt rows
at [Aonohako ff46df5](https://github.com/seo-rii/aonohako/commit/ff46df539edaedef38401dc548d61e10a022e487)
and checks their request, input and ELF hashes. To rebuild the measurement
archive, use the unpacked [corrected Milan probe](../milan/), original v13
shortest-paths boundary payload and original pilot corpus:

```sh
python3 -B prepare.py --aonohako "$AONOHAKO_CHECKOUT" --corrected "$MILAN_PAYLOAD" \
  --boundary-payload "$V13_BOUNDARY_PAYLOAD" --pilot-corpus "$PILOT_CORPUS" --output "$NEW_PAYLOAD"
```

The resulting sibling `.registration.json` matches the published registration
when the same archived executables, helper scripts and runtime are supplied.
The evaluator reuses the original per-process records, kernel exec traces,
counter reconstruction and CPU accounting checks from the Milan study.
