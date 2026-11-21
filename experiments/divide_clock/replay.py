"""Replay one observed main-executable divide block using the v1 register schedule.

Only unsigned 32-bit DIV is parameterized. The optional issue floor is an
occupancy bound per block, independent of the register critical path. The XOR
correction is local to blocks containing an eligible divide.
"""

UINT32 = (1 << 32) - 1
UINT64 = (1 << 64) - 1


def require(condition, message):
    if not condition:
        raise ValueError(message)


def uint(value, high, low=0):
    return type(value) is int and low <= value <= high


def block_x16(block, latency, interval, zero):
    """Return model cycles in sixteenths, including the optional divider floor."""
    require(uint(latency, 255, 1) and uint(interval, 255) and type(zero) is bool, "invalid divider parameters")
    ops = block["ops"]
    eligible = sum(op["opcode"] == "div" and op["div_bits"] == 32 for op in ops)
    issue = max(4 * block["instructions"], 16 * eligible * interval)
    if not block["self_loop"]:
        return issue
    ready = [0] * 1024
    fourth = 0
    for iteration in range(8):
        finish = 0
        for op in ops:
            start = 0
            if not (zero and eligible and op["zero"]):
                for reg in op["reads"]:
                    start = max(start, ready[reg])
            if op["load_delay"]:
                address = max((ready[reg] for reg in op["load_reads"]), default=0)
                start = max(start, address + op["load_delay"])
            result_latency = latency if op["opcode"] == "div" and op["div_bits"] == 32 else op["latency"]
            done = start + result_latency
            for reg in op["writes"]:
                ready[reg] = done
            finish = max(finish, done)
        if iteration == 3:
            fourth = finish
    return max(issue, 4 * (finish - fourth))


def validate(report, pid):
    """Validate a complete sidecar and reconstruct every reported production bound."""
    require(type(report) is dict and set(report) == {"schema", "pid", "threads", "blocks"}, "invalid report fields")
    require(uint(pid, (1 << 31) - 1, 1) and uint(report["pid"], (1 << 31) - 1, 1) and
            report["pid"] == pid and uint(report["schema"], 1, 1) and uint(report["threads"], 1, 1),
            "unexpected process, schema or thread count")
    blocks = report["blocks"]
    require(type(blocks) is list and len(blocks) <= 4096, "invalid block list")
    seen = set()
    for block in blocks:
        require(type(block) is dict and set(block) ==
                {"offset", "entries", "self_loop", "instructions", "v1_x16", "ops"}, "invalid block fields")
        require(uint(block["offset"], UINT64) and uint(block["entries"], UINT64, 1) and
                type(block["self_loop"]) is bool and uint(block["instructions"], 1024, 1) and
                uint(block["v1_x16"], UINT32 - 1, 1), "invalid block metadata")
        ops = block["ops"]
        require(type(ops) is list and len(ops) == block["instructions"], "incomplete instruction descriptor")
        shape = []
        for op in ops:
            require(type(op) is dict and set(op) == {"offset", "opcode", "latency", "div_bits", "zero",
                                                     "reads", "load_reads", "load_delay", "writes"},
                    "invalid instruction fields")
            require(uint(op["offset"], UINT64) and type(op["opcode"]) is str and 0 < len(op["opcode"]) <= 64 and
                    uint(op["latency"], 20, 1) and uint(op["div_bits"], 64) and type(op["zero"]) is bool and
                    uint(op["load_delay"], 4) and op["load_delay"] in (0, 4), "invalid instruction metadata")
            if op["opcode"] in ("div", "idiv"):
                require(op["div_bits"] in (8, 16, 32, 64) and op["latency"] == 20, "invalid divide descriptor")
            else:
                require(op["div_bits"] == 0, "divide width on a non-divide")
            for key in ("reads", "load_reads", "writes"):
                deps = op[key]
                require(type(deps) is list and len(deps) <= 32 and
                        all(uint(slot, 1023) for slot in deps) and sorted(set(deps)) == deps,
                        "invalid dependent register slots")
            require(not op["load_reads"] or op["load_delay"] == 4, "invalid memory dependency")
            if op["zero"]:
                require(op["opcode"] == "xor" and len(op["reads"]) == 1 and
                        op["writes"] == [op["reads"][0], 1023] and not op["load_delay"],
                        "invalid zero idiom")
            shape.append((op["offset"], op["opcode"], op["latency"], op["div_bits"], op["zero"], op["load_delay"],
                          tuple(op["reads"]), tuple(op["load_reads"]), tuple(op["writes"])))
        require(any(op["div_bits"] for op in ops), "block has no divide")
        key = (block["offset"], block["self_loop"], tuple(shape))
        require(key not in seen, "duplicate block identity")
        seen.add(key)
        require(block_x16(block, 20, 0, False) == block["v1_x16"], "v1 block replay differs")
