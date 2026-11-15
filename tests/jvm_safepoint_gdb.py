"""Loaded by GDB only: bounded Linux x86-64 HotSpot signal-context evidence.

This debugger changes execution. Its records are not qualification or proof of
which production-runtime translation path ran. Never substitute a fatal RIP for
the earlier signal-stop PC, the delivered ucontext, or the poll-stub argument.
"""
import json
from pathlib import Path
import struct
import time

import gdb

MAX_EVENTS = 256
stream = Path("jvm-context.jsonl").open("x")
count = 0
truncated = False
installed = set()


def record(event, terminal=False, **fields):
    global count, truncated
    if count >= MAX_EVENTS and not terminal:
        if not truncated:
            stream.write(json.dumps({"schema": 1, "event": "truncated", "limit": MAX_EVENTS}) + "\n")
            stream.flush()
            truncated = True
        return
    count += 1
    thread = gdb.selected_thread()
    row = {"schema": 1, "event": event, "sequence": count, "monotonic_ns": time.monotonic_ns(),
           "inferior_pid": gdb.selected_inferior().pid,
           "thread_ptid": list(thread.ptid) if thread is not None else None, **fields}
    stream.write(json.dumps(row) + "\n")
    stream.flush()


def register(name):
    return int(gdb.selected_frame().read_register(name))


def memory(address, size):
    return bytes(gdb.selected_inferior().read_memory(address, size))


class EntryProbe(gdb.Breakpoint):
    def __init__(self, address, kind):
        super().__init__("*" + hex(address), internal=True)
        self.kind = kind

    def stop(self):
        try:
            architecture = gdb.selected_frame().architecture().name()
            if "x86-64" not in architecture:
                raise ValueError("context offsets require Linux x86-64")
            if self.kind == "poll_stub_argument":
                record(self.kind, entry_pc=hex(register("rip")), polling_pc_argument=hex(register("rdi")),
                       source="SysV first argument at exact SharedRuntime::get_poll_stub entry")
            else:
                # Linux x86-64 glibc ucontext: mcontext starts at byte 40 and
                # contains gregset_t[23]. Capture only fault/register fields;
                # do not inspect or modify signal masks or application state.
                pointer = register("rdx")
                context = memory(pointer, 224)
                fields = {name: hex(struct.unpack_from("<Q", context, 40 + index * 8)[0])
                          for name, index in (("rsp", 15), ("rip", 16), ("err", 19), ("trapno", 20), ("cr2", 22))}
                info = memory(register("rsi"), 24)
                record(self.kind, entry_pc=hex(register("rip")), signal=register("rdi"),
                       siginfo_address=hex(register("rsi")), ucontext_address=hex(pointer),
                       siginfo_code=struct.unpack_from("<i", info, 8)[0],
                       siginfo_fault_address=hex(struct.unpack_from("<Q", info, 16)[0]),
                       delivered_context=fields,
                       source="SysV arguments at exact HotSpot signal-handler entry; not the original kernel context")
        except (gdb.error, ValueError, OverflowError, struct.error) as error:
            record("error", operation=self.kind, detail=str(error)[:300])
        return False


SYMBOLS = {
    "poll_stub_argument": ("SharedRuntime::get_poll_stub(unsigned char*)",),
    "hotspot_signal_context": (
        "PosixSignals::pd_hotspot_signal_handler(int, siginfo*, ucontext*, JavaThread*)",
        "PosixSignals::pd_hotspot_signal_handler(int, siginfo_t*, ucontext_t*, JavaThread*)"),
}


def install_probes(event):
    if "libjvm.so" not in event.new_objfile.filename:
        return
    for kind, symbols in SYMBOLS.items():
        if kind in installed:
            continue
        for symbol in symbols:
            try:
                address = int(gdb.parse_and_eval("(void*)'" + symbol + "'"))
                EntryProbe(address, kind)
                installed.add(kind)
                record("breakpoint_installed", kind=kind, symbol=symbol, address=hex(address))
                break
            except gdb.error:
                continue
        else:
            record("error", operation="resolve_symbol", kind=kind,
                   detail="exact function entry unavailable; no argument/context inference")


def on_stop(event):
    if not isinstance(event, gdb.SignalEvent):
        return
    try:
        fields = {"signal": event.stop_signal, "pc": hex(register("rip")), "sp": hex(register("rsp")),
                  "register_context": {name: hex(register(name)) for name in
                      ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15", "eflags")},
                  "source": "debugger signal-delivery stop before the process handler; may be an engine/code-cache PC"}
        for field, expression in (("siginfo_code", "$_siginfo.si_code"),
                                  ("siginfo_fault_address", "$_siginfo._sifields._sigfault.si_addr")):
            try:
                value = int(gdb.parse_and_eval(expression))
                fields[field] = hex(value) if field.endswith("address") else value
            except gdb.error:
                fields[field] = None
        record("signal_stop", **fields)
    except (gdb.error, ValueError) as error:
        record("error", operation="signal_stop", detail=str(error)[:300])


def on_exit(event):
    record("inferior_exit", terminal=True, exit_code=getattr(event, "exit_code", None))


try:
    gdb.execute("set pagination off")
    gdb.execute("set confirm off")
    gdb.execute("set print thread-events off")
    gdb.execute("set follow-fork-mode parent")
    gdb.execute("set detach-on-fork on")
    gdb.execute("set disable-randomization off")
    gdb.execute("handle SIGILL SIGUSR1 SIGUSR2 SIGCHLD nostop noprint pass")
    # In GDB, noprint also implies nostop. Keep print enabled so these stops
    # actually deliver the original register/siginfo observation to on_stop.
    gdb.execute("handle SIGSEGV SIGBUS SIGABRT stop print pass")
    gdb.events.new_objfile.connect(install_probes)
    gdb.events.stop.connect(on_stop)
    gdb.events.exited.connect(on_exit)
    record("configuration", exact_entry_breakpoints=True, follows_children=False,
           qualifications="diagnostic only; target ASLR retained; original JVM/engine options unchanged")
    gdb.execute("run")
    while gdb.selected_inferior().pid:
        gdb.execute("continue")
except (gdb.error, OSError, ValueError) as error:
    record("error", operation="debugger", detail=str(error)[:300])
finally:
    record("capture_summary", terminal=True, installed_probe_kinds=sorted(installed),
           missing_probe_kinds=sorted(set(SYMBOLS) - installed), event_limit_reached=truncated)
    stream.close()
