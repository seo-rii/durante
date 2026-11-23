"""GDB script: deliver a signal to a thread stopped inside the live boundary routine.

Run by live_work_client.py under `gdb -batch -x`. The routine is the client's
own context switch, so the interesting interleavings are single instructions
wide: this stops the application thread at one chosen point of the routine or
of its callback, resumes it with SIGUSR1 already pending, and reports where it
stopped and how the fixture exited. The fixture checks its own state, and
executes a breakpoint instruction once its handler is installed.
"""
import json
import os
from pathlib import Path
import re

import gdb

CLIENT = Path(os.environ["DURANTE_GDB_CLIENT"]).resolve()
POINT = os.environ["DURANTE_GDB_POINT"]
RESULT = Path(os.environ["DURANTE_GDB_RESULT"])
# Link-time addresses of the client's lowest segment, of the callback and of
# the variable holding the routine's address, then the callback's first bytes.
BASE, CALLBACK, ROUTINE = (int(os.environ["DURANTE_GDB_" + name], 16)
                           for name in ("BASE", "CALLBACK", "ROUTINE"))
CALLBACK_BYTES = bytes.fromhex(os.environ["DURANTE_GDB_CALLBACK_BYTES"])


def running():
    return any(thread.is_valid() for thread in gdb.selected_inferior().threads())


def mappings():
    """(start, end, file offset, name) of each mapping that has a name."""
    for line in Path(f"/proc/{gdb.selected_inferior().pid}/maps").read_text().splitlines():
        fields = line.split(maxsplit=5)
        if len(fields) == 6:
            start, end = (int(bound, 16) for bound in fields[0].split("-"))
            yield start, end, int(fields[2], 16), fields[5]


def client_slide():
    """Where the runtime's loader put the client, relative to its link address."""
    bases = [start for start, _, offset, name in mappings() if offset == 0 and Path(name) == CLIENT]
    return min(bases) - BASE if bases else None


def callback_address():
    slide = client_slide()
    if slide is None:
        return None
    try:
        mapped = bytes(gdb.selected_inferior().read_memory(CALLBACK + slide, len(CALLBACK_BYTES)))
    except gdb.MemoryError:
        return None
    return CALLBACK + slide if mapped == CALLBACK_BYTES else None


def routine_points(routine):
    """Instruction addresses of the routine that name its distinct states."""
    listing = gdb.execute(f"x/40i {routine}", to_string=True)
    code = [(int(row.group(1), 16), row.group(2)) for row in
            re.finditer(r"^\s*(0x[0-9a-f]+)[^:]*:\s*(\S+)", listing, re.MULTILINE)]

    def first(prefix):  # Some versions print a size suffix: pushfq, callq, jmpq.
        return next(index for index, (_, mnemonic) in enumerate(code) if mnemonic.startswith(prefix))

    call = first("call")
    return {"entry": code[0][0],                # application stack and flags
            "switched": code[first("pushf")][0],  # private stack, nothing saved
            "call": code[call][0],              # everything saved, flags cleared
            "returned": code[call + 1][0],      # callback done, nothing restored
            "resume": code[first("jmp")][0]}    # all restored but the two scratch registers


def main():
    gdb.execute("set pagination off")
    gdb.execute("set confirm off")
    # The runtime raises and handles faults of its own; none of them is ours.
    for name in ("SIGUSR1", "SIGSEGV", "SIGBUS", "SIGILL", "SIGALRM"):
        gdb.execute(f"handle {name} nostop noprint pass", to_string=True)
    # A syscall catchpoint would find the client's mapping, but this debugger
    # then stops at every later syscall of the runtime. The fixture's own
    # breakpoint instruction stops here once, with the client long mapped.
    gdb.execute("run", to_string=True)
    if not running() or int(gdb.parse_and_eval("$_siginfo.si_signo")) != 5:  # SIGTRAP
        raise RuntimeError("the fixture did not reach its breakpoint instruction")
    callback = callback_address()
    if callback is None:
        raise RuntimeError("the client's callback is not mapped")
    pointer = gdb.lookup_type("unsigned long").pointer()
    routine = int(gdb.Value(ROUTINE + callback - CALLBACK).cast(pointer).dereference())
    target = callback if POINT == "callback" else routine_points(routine)[POINT]
    gdb.execute(f"break *{target}", to_string=True)
    gdb.execute("signal 0", to_string=True)  # Resume without the trap.
    stopped = int(gdb.parse_and_eval("$pc"))
    stack = int(gdb.parse_and_eval("$rsp"))
    application = any(start <= stack <= end for start, end, _, name in mappings() if name == "[stack]")
    gdb.execute("delete")
    gdb.execute("signal SIGUSR1", to_string=True)
    code = gdb.parse_and_eval("$_exitcode")
    RESULT.write_text(json.dumps({
        "point": POINT, "target": target, "stopped": stopped, "application_stack": application,
        "exit_code": None if code.type.code == gdb.TYPE_CODE_VOID else int(code)}))


try:
    main()
except Exception as error:  # Report instead of leaving the test to time out.
    RESULT.write_text(json.dumps({"point": POINT, "error": f"{type(error).__name__}: {error}"}))
    if running():
        gdb.execute("kill")
