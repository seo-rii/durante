"""Decode the little-endian DSPAN001 wire format into the original schema-2 fields."""
import struct

HEADER = struct.Struct("<8s10Q")
ROW = struct.Struct("<6Q")
MAX_SITES = 16384
FIELDS = ("offset", "loads", "pairs", "near_pairs", "far_pairs", "sampled_address_span_bytes")


def decode_packed(raw):
    if not HEADER.size <= len(raw) <= HEADER.size + MAX_SITES * ROW.size:
        raise ValueError("invalid packed receipt size")
    magic, schema, pid, threads, period, line, loads, pairs, near, far, count = HEADER.unpack_from(raw)
    if (magic, schema, threads, period, line) != (b"DSPAN001", 2, 1, 1024, 64) or pid == 0:
        raise ValueError("wrong packed receipt header")
    if count > MAX_SITES or len(raw) != HEADER.size + count * ROW.size:
        raise ValueError("packed row count disagrees with receipt size")
    sites = [dict(zip(FIELDS, row, strict=True)) for row in ROW.iter_unpack(memoryview(raw)[HEADER.size:])]
    return {"schema": schema, "pid": pid, "threads": threads, "period": period, "line_bytes": line,
            "loads": loads, "pairs": pairs, "near_pairs": near, "far_pairs": far, "sites": sites}
