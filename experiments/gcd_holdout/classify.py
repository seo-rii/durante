"""Classify observed DIV32 back edges using only exact x86-64 ELF text and offsets.

The accepted straight-line language is intentionally small. Each body may use
32-bit register MOV, zeroing XOR/MOV-immediate-zero, DIV r32, DEC/SUB-one,
TEST r32,r32 or CMP r32,0, and a one-byte NOP. The closing instruction must be
short or near JNZ targeting the first body byte. Unsupported forms return
'other'. Register renaming and moves preserve value provenance.
"""
import struct

ELF = struct.Struct("<16sHHIQQQIHHHHHH")
PHDR = struct.Struct("<IIQQQQQQ")
ZERO = ("zero",)
OTHER = "other"


def executable_slice(binary, target, branch):
    """Translate module-relative PCs through a single executable file-backed PT_LOAD."""
    if (type(target) is not int or type(branch) is not int or target < 0
            or not 0 < branch - target <= 256 or len(binary) < ELF.size):
        return None
    ident, kind, isa, version, _, phoff, _, _, ehsize, phsize, count, *_ = ELF.unpack_from(binary)
    if (ident[:7] != b"\x7fELF\x02\x01\x01" or kind not in (2, 3) or isa != 62 or version != 1
            or ehsize != ELF.size or phsize != PHDR.size or not 0 < count < 65535
            or phoff < ehsize or phoff + count * phsize > len(binary)):
        return None
    loads = []
    for i in range(count):
        typ, flags, off, addr, _, size, memsize, _ = PHDR.unpack_from(binary, phoff + i * phsize)
        if typ == 1:
            if off + size > len(binary) or size > memsize or (off - addr) % 4096:
                return None
            loads.append((flags, off, addr, size, memsize))
    if not loads:
        return None
    base = min(addr for _, _, addr, _, _ in loads) & ~4095
    start, end = base + target, base + branch
    covering = [seg for seg in loads if seg[2] <= start and end + 2 <= seg[2] + seg[3]]
    if len(covering) != 1:
        return None
    segment = covering[0]
    flags, off, addr, size, _ = segment
    if not flags & 1 or flags & 2:
        return None
    lo, hi = off + start - addr, off + min(end + 6 - addr, size)
    result = binary[lo:hi]
    width = 6 if result[branch - target:branch - target + 2] == b"\x0f\x85" else 2
    if any(seg is not segment and start < seg[2] + seg[4] and seg[2] < end + width
           for seg in loads):
        return None
    return result


def decode_body(body):
    """Decode only explicitly supported register-only instructions."""
    ops = []
    i = 0
    while i < len(body):
        op = body[i]
        i += 1
        if op == 0x90:
            continue
        rex = 0
        if op in (0x40, 0x41, 0x44, 0x45):
            rex = op
            if i >= len(body):
                return None
            op = body[i]
            i += 1
        if 0xB8 <= op <= 0xBF and not rex & 4:
            if body[i:i + 4] != bytes(4):
                return None
            ops.append(("zero", op - 0xB8 + 8 * (rex & 1), None))
            i += 4
            continue
        if i >= len(body):
            return None
        modrm = body[i]
        i += 1
        if modrm >> 6 != 3:
            return None
        subgroup = (modrm >> 3) & 7
        reg = subgroup + (8 if rex & 4 else 0)
        rm = (modrm & 7) + 8 * (rex & 1)
        if op in (0x89, 0x8B):
            dest, src = (rm, reg) if op == 0x89 else (reg, rm)
            ops.append(("move", dest, src))
        elif op in (0x31, 0x33) and reg == rm:
            ops.append(("xor_zero", reg, None))
        elif op == 0x85 and reg == rm:
            ops.append(("test", reg, None))
        elif op == 0xF7 and subgroup == 6 and not rex & 4:
            ops.append(("div", rm, None))
        elif op == 0xFF and subgroup == 1 and not rex & 4:
            ops.append(("dec", rm, None))
        elif op in (0x81, 0x83) and subgroup in (5, 7) and not rex & 4:
            width = 4 if op == 0x81 else 1
            expected = 1 if subgroup == 5 else 0
            if body[i:i + width] != expected.to_bytes(width, "little"):
                return None
            i += width
            ops.append(("dec" if subgroup == 5 else "test", rm, None))
        else:
            return None
    return ops


def site_mode(binary, target_offset, branch_offset):
    """Return 'remainder', 'counted', or 'other' for one observed closing branch."""
    code = executable_slice(binary, target_offset, branch_offset)
    if code is None:
        return OTHER
    span = branch_offset - target_offset
    jump = code[span:]
    if jump.startswith(b"\x75"):
        end, displacement = 2, jump[1:2]
    elif jump.startswith(b"\x0f\x85") and len(jump) >= 6:
        end, displacement = 6, jump[2:6]
    else:
        return OTHER
    if span + end + int.from_bytes(displacement, "little", signed=True) != 0:
        return OTHER
    body = decode_body(code[:span])
    if body is None or sum(op == "div" for op, _, _ in body) != 1:
        return OTHER

    regs = [("entry", r) for r in range(16)]
    divides, conditions = [], []
    for turn in range(2):
        flag = None
        for op, dst, src in body:
            if op == "move":
                regs[dst] = regs[src]
            elif op in ("zero", "xor_zero"):
                regs[dst] = ZERO
                if op == "xor_zero":
                    flag = ZERO
            elif op == "div":
                if regs[2] != ZERO:
                    return OTHER
                divides.append((regs[0], regs[dst]))
                regs[0], regs[2] = ("quotient", turn), ("remainder", turn)
                flag = None  # DIV leaves ZF undefined.
            elif op == "dec":
                regs[dst] = ("dec", regs[dst])
                flag = regs[dst]
            elif op == "test":
                flag = regs[dst]
        conditions.append(flag)

    first_a, first_b = divides[0]
    if (first_a[0] != "entry" or first_b[0] != "entry" or first_a == first_b
            or divides[1] != (first_b, ("remainder", 0))):
        return OTHER
    if conditions == [("remainder", 0), ("remainder", 1)]:
        return "remainder"
    first = conditions[0]
    if (first and first[0] == "dec" and first[1][0] == "entry"
            and first[1] not in (first_a, first_b) and conditions[1] == ("dec", first)):
        return "counted"
    return OTHER


def sites(binary, receipt):
    """Classify all site offsets in a previously validated generic trip receipt."""
    return {s["branch_offset"]: site_mode(binary, s["target_offset"], s["branch_offset"])
            for s in receipt["sites"]}
