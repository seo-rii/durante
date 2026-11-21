# DIV32 loop-trip probe

This optional DynamoRIO client counts visits to a conditional backward branch enclosing an unsigned 32-bit x86 divide. A taken branch continues the traversal. Fallthrough completes it. Each site records total branch visits, completed traversal count, changes between consecutive completed lengths, last completed length, and the number of visits in the current partial traversal. The first completion establishes the reference length. Counts describe branch outcomes. A hardware branch predictor can behave differently on the same outcomes.

The selector examines the main executable's original instructions. It accepts a direct flags-based conditional jump to a lower address when the interval from its target to the jump is at most 256 bytes, contains a DIV32 and has no interior control transfer or system call. It decodes that interval independently of the translated basic block. In particular, the first visit can start before the branch target and share the same counter as later visits. The study uses immutable code, one process, one thread and disabled traces. A visit denotes execution reaching the closing branch, including when application control initially enters partway through the body.

Each matching visit increments a per-site counter. The common path inserts an INC before a flag-writing application instruction. Otherwise, a saved scratch register, MOV and LEA preserve live flags. A second copy of the application's condition skips the clean call on taken branches. The fallthrough clean call uses general-purpose registers. Site addresses in the output are relative to the main module's start.

## Build and run

From the repository root, set `DR_SDK` to the DynamoRIO 11.3 SDK root and `DRRUN` to the launcher's full path. Give the process a fresh path for its JSON receipt.

```sh
src=experiments/gcd_controls/trip_probe
cmake -S "$src" -B /tmp/trip-build -DCMAKE_BUILD_TYPE=Release \
    -DDynamoRIO_DIR="$DR_SDK/cmake"
cmake --build /tmp/trip-build
"$DRRUN" -quiet -client /tmp/trip-build/libtrip_probe.so 00000001 \
    '-out /tmp/trip.json' -disable_traces -- /path/to/program
```

The registered collector also loads the version-9001 meter as client `00000000` and writes that meter's receipts separately. The JSON validator is `receipt.py::validate(data, pid)`.

## Hand oracle

```sh
python3 experiments/gcd_controls/trip_probe/check.py \
    "$DRRUN" /tmp/trip-build/libtrip_probe.so
```

The check compiles and executes an x86-64 fixture twice, natively and under the probe. Two loop shapes check a first-entry fallthrough and an explicitly split header. One uses INC before DEC. The other places a NOP between DEC and JNZ to exercise the flag-preserving path. Both check general-purpose register values, XMM data and the fallthrough condition. Traversal lengths 1, 1, 3, 2 must yield seven visits, four completions and two changes at each site. A separate fixture handles an arithmetic signal and exits after three taken visits with zero completions. The registered timing corpus uses uninterrupted runs.
