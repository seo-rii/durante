"""The live boundary callback must not need a saved vector or x87 state.

Live block entries reach the callback through a routine that saves only the
general registers the ABI lets a callee change. That is correct only while no
instruction the callback can reach touches any other register, so this reads
the built client: everything reachable from the callback is general-register
code, and it leaves the client only on the path that ends the process.
"""
import os
import platform
import re
import shutil
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CALLBACK = "charge_boundary_state"
# fail() reports and aborts. What it calls never returns to the application.
FATAL = {"fail"}
OTHER_STATE = re.compile(r"%(?:[xyz]?mm\d|st\b|k[0-7]\b)")
OTHER_MNEMONIC = re.compile(r"(?:f|v)\w+|emms|ldmxcsr|stmxcsr|xsave\w*|xrstor\w*")
TARGET = re.compile(r"<([^>+]+)(?:\+0x[0-9a-f]+)?>$")


def disassemble(library):
    """Map each function to its (mnemonic, operands) instructions."""
    text = subprocess.run(["objdump", "-d", "--no-show-raw-insn", str(library)],
                          capture_output=True, text=True, check=True).stdout
    functions, current = {}, None
    for line in text.splitlines():
        header = re.fullmatch(r"[0-9a-f]+ <(.+)>:", line)
        if header:
            current = functions.setdefault(header.group(1), [])
        elif current is not None and "\t" in line:
            words = line.split("\t", 1)[1].split()
            while words and words[0] in ("lock", "notrack", "bnd", "rep", "repz", "repnz", "data16", "cs", "ds"):
                words.pop(0)
            if words:
                current.append((words[0], " ".join(words[1:])))
    return functions


def violations(functions, entry=CALLBACK, fatal=FATAL):
    """Instructions reachable from entry that use more than the general registers."""
    if entry not in functions:
        return [f"{entry} is not a function of the client"]
    found, seen, pending = [], set(), [entry]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        for mnemonic, operands in functions[name]:
            where = f"{name}: {mnemonic} {operands}".rstrip()
            if OTHER_STATE.search(operands) or OTHER_MNEMONIC.fullmatch(mnemonic):
                found.append(where)
            if not (mnemonic.startswith("call") or mnemonic.startswith("j")):
                continue
            target = TARGET.search(operands)
            if not target:
                # An indirect transfer could reach anything.
                found.append(where)
            elif target.group(1) in fatal:
                continue
            elif target.group(1) not in functions or target.group(1).endswith("@plt"):
                found.append(where)
            else:
                pending.append(target.group(1))
    return found


class BoundaryCallbackTests(unittest.TestCase):
    def test_reader_reports_each_kind_of_escape(self):
        functions = {
            "entry": [("mov", "%rdi,%rax"), ("call", "1000 <helper>"), ("jne", "2000 <entry.cold>"),
                      ("call", "3000 <fail>"), ("ret", "")],
            "entry.cold": [("jmp", "1010 <helper+0x10>")],
            "helper": [("lea", "0x8(%rdi),%rax"), ("ret", "")],
            "fail": [("movaps", "%xmm0,(%rsp)"), ("call", "4000 <dr_abort@plt>")],
            "dr_abort@plt": [("jmp", "*0x2fe2(%rip)")],
        }
        self.assertEqual(violations(functions, "entry"), [])
        for name, instruction in (("helper", ("movdqu", "(%rsi),%xmm1")),
                                  ("helper", ("vzeroupper", "")),
                                  ("helper", ("fldpi", "")),
                                  ("helper", ("kmovw", "%k1,%eax")),
                                  ("entry.cold", ("call", "5000 <memcpy@plt>")),
                                  ("entry.cold", ("call", "*%rax")),
                                  ("helper", ("jmp", "6000 <unknown>"))):
            changed = dict(functions, **{name: [instruction, *functions[name]]})
            self.assertEqual(len(violations(changed, "entry")), 1, instruction)
        self.assertEqual(len(violations(functions, "missing")), 1)

    def test_built_callback_uses_only_general_registers(self):
        if platform.system() != "Linux" or platform.machine() != "x86_64":
            self.skipTest("the client is built for Linux x86-64 only")
        configured = os.environ.get("DURANTE_TEST_CLIENT")
        client = Path(configured) if configured else ROOT / "build/libdurante.so"
        required = configured or os.environ.get("GITHUB_ACTIONS")
        if not client.is_file() or not shutil.which("objdump"):
            if required:
                self.fail(f"cannot disassemble the client at {client}")
            self.skipTest("build the client or set DURANTE_TEST_CLIENT to check its callback")
        functions = disassemble(client)
        self.assertGreater(len(functions.get(CALLBACK, [])), 10)
        self.assertEqual(violations(functions), [])


if __name__ == "__main__":
    unittest.main()
