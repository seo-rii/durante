"""Check the opcode boundary, two-iteration recurrence and archived receipts."""
import gzip
import hashlib
import json
from pathlib import Path
import struct
import tarfile
import unittest

from classify import site_mode, sites

ROOT = Path(__file__).resolve().parents[2]
GCD = bytes.fromhex("89 f8 89 d7 31 d2 f7 f7")
REM = bytes.fromhex("85 d2")
COUNT = bytes.fromhex("ff c9 85 c9")


def image(body, *, near=False, base=0, writable=False, overlap=False, target_error=0):
    # Two real PT_LOAD mappings. File text and module-relative target deliberately differ.
    load_text, file_text = 0x3000, 0x1000
    branch = load_text + len(body)
    jump_len = 6 if near else 2
    disp = -len(body) - jump_len + target_error
    jump = b"\x0f\x85" + struct.pack("<i", disp) if near else b"\x75" + struct.pack("b", disp)
    text = body + jump
    count = 3 if overlap else 2
    hdr = struct.pack("<16sHHIQQQIHHHHHH", b"\x7fELF\x02\x01\x01", 2 if base else 3, 62, 1,
                      base + load_text, 64, 0, 0, 64, 56, count, 0, 0, 0)
    def segment(flags, offset, addr, size):
        return struct.pack("<IIQQQQQQ", 1, flags, offset, addr, addr, size, size, 4096)
    hdr += segment(4, 0, base, 64 + 56 * count)
    hdr += segment(7 if writable else 5, file_text, base + load_text, len(text))
    if overlap:
        hdr += segment(4, file_text, base + load_text, 1)
    return hdr.ljust(file_text, b"\0") + text, load_text, branch


class ClassifierTests(unittest.TestCase):
    def test_modes(self):
        for body, mode in [
            (GCD + REM, "remainder"),
            (GCD + COUNT, "counted"),
            (GCD + bytes.fromhex("ff c9"), "counted"),
            (GCD + bytes.fromhex("83 e9 01 83 f9 00"), "counted"),
            (GCD + bytes.fromhex("ff c9 83 fa 00"), "remainder"),
            # R8D holds the divisor, EDX uses MOV-immediate-zero, CMP tests the remainder.
            (bytes.fromhex("44 89 c0 41 89 d0 ba 00000000 41 f7 f0 83 fa 00"), "remainder"),
            (bytes.fromhex("8b c7 8b fa 33 d2 f7 f7 89 d6 85 f6"), "remainder"),
        ]:
            for base in (0, 0x400000):
                for near in (True, False):
                    with self.subTest(body=body.hex(), base=base, near=near):
                        self.assertEqual(site_mode(*image(body, base=base, near=near)), mode)

    def test_unsupported_and_wrong_recurrences(self):
        for body in [
            bytes.fromhex("89 f8 89 d7 f7 f7 85 d2"),  # no zero high half
            bytes.fromhex("89 f8 89 cf 31 d2 f7 f7 85 d2"),  # next divisor from invariant ECX
            bytes.fromhex("89 c8 89 d7 31 d2 f7 f7 85 d2"),  # next dividend from invariant ECX
            bytes.fromhex("89 f8 89 d7 31 d2 48 f7 f7 85 d2"),  # 64-bit
            bytes.fromhex("89 f8 89 d7 31 d2 f7 ff 85 d2"),  # signed
            bytes.fromhex("89 f8 89 d7 31 d2 f7 37 85 d2"),  # memory operand
            GCD, GCD + bytes.fromhex("85 c0"),  # undefined ZF, or quotient
            GCD + bytes.fromhex("85 c9"),  # independent, unchanging value
            GCD + bytes.fromhex("89 f9 ff c9"),  # decremented divisor copy
            GCD + bytes.fromhex("83 e9 02"),  # changing by two
            GCD + bytes.fromhex("ff c9 83 f9 03"),  # unsupported compare threshold
            GCD + bytes.fromhex("f7 f1 85 d2"),  # two DIVs
            GCD + bytes.fromhex("eb 00 85 d2"),  # interior jump
            GCD + bytes.fromhex("41 90 85 d2"),  # XCHG EAX,R8D is not NOP
        ]:
            with self.subTest(body=body.hex()):
                self.assertEqual(site_mode(*image(body)), "other")

    def test_elf_and_backedge(self):
        for kwargs in ({"target_error": 1}, {"writable": True}, {"overlap": True}):
            self.assertEqual(site_mode(*image(GCD + REM, **kwargs)), "other")
        data, start, end = image(GCD + REM)
        for bad in (b"", data[:62], data[:-1], data[:-2] + b"\x74" + data[-1:],
                    data[:18] + b"\x03\x00" + data[20:]):
            self.assertEqual(site_mode(bad, start, end), "other")
        for a, b in ((-1, end), (end, start), (start + 1, end), (start, end - 1)):
            self.assertEqual(site_mode(data, a, b), "other")

    def test_archived_guest_sites(self):
        expected = {
            "gcd": ["remainder"], "loop_remainder_original": ["remainder"],
            "loop_remainder_grouped": ["remainder"], "loop_counted_original": ["counted"],
            "loop_counted_grouped": ["counted"], "floor_groups": ["other"],
            "batch_quotients": [], "stream_divisor": ["other"], "stream_dividend": ["other"],
            "stream_independent": ["other"],
        }
        protocol = json.loads((ROOT / "experiments/gcd_controls/protocol.json").read_bytes())
        result = ROOT / "experiments/results/gce-gcd-controls/dataset.json.gz"
        rows = json.loads(gzip.decompress(result.read_bytes()))["samples"]
        self.assertEqual({case["workload_id"] for case in protocol["cases"]}, set(expected))
        for case in protocol["cases"]:
            with tarfile.open(ROOT / case["binary_archive"]) as archive:
                binary = archive.extractfile(case["archive_member"]).read()
            self.assertEqual(hashlib.sha256(binary).hexdigest(), case["binary_sha256"])
            name = case["workload_id"]
            samples = [row for row in rows if row["workload_id"] == name]
            self.assertEqual(len(samples), 4)
            for row in samples:
                self.assertEqual(list(sites(binary, row["trips"]["trip"]).values()), expected[name])


if __name__ == "__main__":
    unittest.main()
