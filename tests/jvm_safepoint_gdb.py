"""GDB-only observation of the pinned native runtime's signal translation.

Application-entry software breakpoints are unsuitable under a code cache: their
INT3 can execute at an unrecognized cache address. Observe the native runtime's
translation boundary instead. These records are not HotSpot handler/poll-stub
arguments and never establish that a debugger run reproduces a production bug.
"""
import json
from pathlib import Path
import time

import gdb

MAX_EVENTS = 256
MAX_RAW_SIGNALS = 16
MAX_PENDING_RETURNS = 16
MAX_SIGNAL_THREADS = 64
stream = Path("jvm-context.jsonl").open("x")
configuration_path = Path("jvm-context-config.json")
configuration = json.loads(configuration_path.read_text()) if configuration_path.exists() else {}
count = 0
truncated = False
installed = set()
install_attempted = False
pending = {}
last_signals = {}
raw_signals = 0
translations = 0
returns = 0
critical_translations = 0
critical_sample = None
context_type = None


def record(event, terminal=False, **fields):
    global count, truncated
    if count >= MAX_EVENTS and not terminal:
        if not truncated:
            stream.write(json.dumps({"schema": 2, "event": "truncated", "limit": MAX_EVENTS}) + "\n")
            stream.flush()
            truncated = True
        return
    count += 1
    thread = gdb.selected_thread()
    row = {"schema": 2, "event": event, "sequence": count, "monotonic_ns": time.monotonic_ns(),
           "inferior_pid": gdb.selected_inferior().pid,
           "thread_ptid": list(thread.ptid) if thread is not None else None, **fields}
    stream.write(json.dumps(row) + "\n")
    stream.flush()


def register(name):
    return int(gdb.selected_frame().read_register(name))


def context(address):
    # Use the verified runtime's DWARF type rather than borrowing a libc layout.
    value = gdb.Value(address).cast(context_type.pointer()).dereference()["uc_mcontext"]
    return {name: hex(int(value[name])) for name in ("rip", "rsp", "err", "trapno", "cr2")}


class TranslationReturn(gdb.FinishBreakpoint):
    def __init__(self, identifier, pointer, before):
        super().__init__(gdb.newest_frame(), internal=True)
        self.identifier, self.pointer, self.before = identifier, pointer, before

    def stop(self):
        global returns, critical_translations, critical_sample
        try:
            if self.return_value is None:
                raise ValueError("typed translation return value unavailable")
            result = int(self.return_value)
            if result not in (0, 1):
                raise ValueError("translation return value is not boolean")
            after = context(self.pointer)
            returns += 1
            if not result or after["rip"] == "0x0":
                critical_translations += 1
                if critical_sample is None:
                    critical_sample = {"translation_id": self.identifier,
                                       "inferior_pid": gdb.selected_inferior().pid,
                                       "ucontext_address": hex(self.pointer),
                                       "thread_ptid": list(gdb.selected_thread().ptid),
                                       "pre_context": self.before, "post_context": after,
                                       "result": bool(result)}
            record("translation_return", translation_id=self.identifier,
                   ucontext_address=hex(self.pointer), result=bool(result),
                   post_context=after, pc_changed=after["rip"] != self.before["rip"],
                   source="typed bool return and ucontext after native translate_sigcontext")
        except (gdb.error, ValueError, OverflowError) as error:
            record("error", operation="translation_return", translation_id=self.identifier, detail=str(error)[:300])
        finally:
            pending.pop(self.identifier, None)
        return False

    def out_of_scope(self):
        pending.pop(self.identifier, None)
        record("error", operation="translation_return", translation_id=self.identifier,
               detail="native translation frame left scope without an observed return")


class TranslationEntry(gdb.Breakpoint):
    def stop(self):
        global translations
        try:
            if len(pending) >= MAX_PENDING_RETURNS:
                raise ValueError("pending native translation return limit reached")
            pointer = register("rsi")
            before = context(pointer)
            translations += 1
            identifier = translations
            thread = tuple(gdb.selected_thread().ptid)
            signal = last_signals.get(thread)
            # A preceding stop is only a correlation candidate, not proof that
            # unrelated native calls belong to the same signal delivery.
            matches = signal is not None and signal["pc"] == before["rip"]
            record("translation_enter", translation_id=identifier,
                   entry_pc=hex(register("rip")), ucontext_address=hex(pointer),
                   avoid_failure=bool(register("rdx") & 0xff), fragment_address=hex(register("rcx")),
                   pre_context=before, preceding_signal_stop=signal if matches else None,
                   source="SysV arguments at exact native translate_sigcontext entry; not a HotSpot argument")
            pending[identifier] = TranslationReturn(identifier, pointer, before)
        except (gdb.error, ValueError, OverflowError) as error:
            record("error", operation="translation_enter", detail=str(error)[:300])
        return False


def install_probes():
    global context_type, install_attempted
    if installed or install_attempted or not configuration:
        return
    try:
        expected = Path(configuration["core"]).resolve()
        debug = Path(configuration["debug_file"]).resolve()
        objfiles = [item for item in gdb.objfiles() if Path(item.filename).resolve() == expected]
        if not objfiles:
            return
        install_attempted = True
        pid = gdb.selected_inferior().pid
        mappings = [line.split(None, 5) for line in Path("/proc/%d/maps" % pid).read_text().splitlines()]
        mappings = [fields for fields in mappings if len(fields) == 6 and Path(fields[5]).resolve() == expected]
        # Objfile callbacks precede a usable stopped mapping on this runtime.
        # Resolve the already-loaded native symbols only at an actual signal
        # stop, and reject any address outside the declared runtime mapping.
        if len(objfiles) != 1:
            raise ValueError("unique native runtime symbol file required")
        objfile = objfiles[0]
        companions = [item for item in gdb.objfiles() if Path(item.filename).resolve() == debug
                      and item.owner is objfile]
        if len(companions) != 1 or not objfile.build_id or companions[0].build_id != objfile.build_id:
            raise ValueError("matching runtime and separate debug GNU build IDs required")
        architecture = gdb.selected_frame().architecture().name()
        if "x86-64" not in architecture:
            raise ValueError("entry argument registers require Linux x86-64")
        context_type = gdb.lookup_type("kernel_ucontext_t")
        address = int(gdb.parse_and_eval("(void*)translate_sigcontext"))
        # An exact symbol must resolve inside this native runtime's executable
        # mapping. Never install an application/code-cache entry breakpoint.
        matched = False
        for fields in mappings:
            if "x" not in fields[1]:
                continue
            start, end = (int(value, 16) for value in fields[0].split("-"))
            if start <= address < end:
                matched = True
        if not matched:
            raise ValueError("translation symbol is outside the declared native runtime mapping")
        TranslationEntry("*" + hex(address), internal=True)
        installed.add("native_translation")
        record("breakpoint_installed", kind="native_translation", symbol="translate_sigcontext",
               address=hex(address), core=str(expected), debug_file=str(debug), build_id=objfile.build_id)
    except (gdb.error, OSError, ValueError, KeyError, AttributeError) as error:
        record("error", operation="install_native_probe", detail=str(error)[:300])


def on_stop(event):
    global raw_signals
    if not isinstance(event, gdb.SignalEvent):
        return
    try:
        raw_signals += 1
        fields = {"signal": event.stop_signal, "pc": hex(register("rip")), "sp": hex(register("rsp")),
                  "observed_monotonic_ns": time.monotonic_ns(), "raw_signal_index": raw_signals,
                  "source": "debugger signal-delivery stop before the process handler; may be engine/code-cache PC"}
        for field, expression in (("siginfo_code", "$_siginfo.si_code"),
                                  ("siginfo_fault_address", "$_siginfo._sifields._sigfault.si_addr")):
            try:
                value = int(gdb.parse_and_eval(expression))
                fields[field] = hex(value) if field.endswith("address") else value
            except gdb.error:
                fields[field] = None
        thread = tuple(gdb.selected_thread().ptid)
        if thread not in last_signals and len(last_signals) >= MAX_SIGNAL_THREADS:
            raise ValueError("signal-context thread limit reached")
        last_signals[thread] = fields
        if raw_signals <= MAX_RAW_SIGNALS:
            record("signal_stop", **fields)
        install_probes()
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
    # noprint implies nostop in GDB; preserve delivery stops and bounded logging.
    gdb.execute("handle SIGSEGV SIGBUS SIGABRT stop print pass")
    gdb.events.stop.connect(on_stop)
    gdb.events.exited.connect(on_exit)
    record("configuration", probe_domain="dynamorio_native_translation", follows_children=False,
           raw_signal_sample_limit=MAX_RAW_SIGNALS, event_limit=MAX_EVENTS,
           qualifications="diagnostic only; target ASLR and original JVM/engine options retained")
    gdb.execute("run")
    while gdb.selected_inferior().pid:
        gdb.execute("continue")
except (gdb.error, OSError, ValueError) as error:
    record("error", operation="debugger", detail=str(error)[:300])
finally:
    record("capture_summary", terminal=True, installed_probe_kinds=sorted(installed),
           missing_probe_kinds=sorted({"native_translation"} - installed),
           event_limit_reached=truncated, raw_signal_stops_seen=raw_signals,
           raw_signal_stops_sampled_out=max(0, raw_signals - MAX_RAW_SIGNALS),
           translations_entered=translations, translations_returned=returns,
           pending_returns=len(pending), application_entry_breakpoints=False,
           critical_translations=critical_translations, first_critical_translation=critical_sample)
    stream.close()
