# Guarded scalar-read sampler

This diagnostic Linux x86-64 client retains the #80 profiled sampler’s per-site phase, schema-2 output and main-ELF-relative offsets. It also counts executions of every eligible read instruction outside the selected set. A harness binds the ordered decimal offset list to the exact ELF before launch.

Build with the release DynamoRIO 11.3 SDK and use the client in the second slot after the release v11 meter.

```sh
cmake -S experiments/guarded_sampler/probe -B "$build" -DCMAKE_BUILD_TYPE=Release -DDynamoRIO_DIR="$sdk/cmake"
cmake --build "$build"
python3 experiments/guarded_sampler/probe/check_controls.py --drrun "$release/bin64/drrun" \
    --meter "$meter" --span "$span" --guarded "$build/libsampler_guarded.so"
```

The client accepts `-out sample.json -sites 4193 -guard guard.json`. Both output paths must be new. The second report has the following exact shape, illustrated by the shifted path-mix workload.

```json
{"schema":1,"arm":"guarded","scope":"main_module_eligible_scalar_reads","pid":1234,"threads":1,"omitted_loads":7600000}
```

For each dynamic basic block, translation finds omitted reads using #78’s opcode, operand and module rules. The analysis places a DynamoRIO label before the last omitted read in each straight-line group, splitting at control transfers, system calls, interrupts, module boundaries and every 64 omitted instructions. A nonempty group performs one inline increment in a shared bin indexed by its static number of omitted reads. Empty groups add zero. The checked weighted sum of these bins at exit is `omitted_loads`. The sum of `sample.loads` and `guard.omitted_loads` gives the eligible instruction visits for this execution.

The increment preserves CF. When the following application instructions overwrite the other arithmetic flags before reading them, it uses INC. Otherwise it saves and restores RCX and uses MOV, LEA and JRCXZ. Both paths detect unsigned wrap. The client refuses a bin wrap, weighted sum overflow, combined sum overflow, observed application signal, second thread or fork. The guard contains only the aggregate. It can detect a change in overall selected coverage without identifying an omitted site or its access pattern.

Use these receipts with matching process identity and a normally completed, uninterrupted, single-threaded release v11 record. A group is charged immediately before its last omitted instruction. A fault, asynchronous control transfer, or interrupted exit can prevent the earlier instructions in that group from being represented correctly. The caller validates completion and the program output before using the result. The measured unit is a dynamic eligible scalar read, including explicit stack operands. REP traffic, stores, vector operations, implicit stack accesses and other unsupported operands are outside this count.
