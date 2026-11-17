//! Collection of bounded, untrusted lifecycle records after a process tree exits.
//!
//! Structural consistency is not integrity against an application that can
//! modify its own instrumentation state. Every result remains diagnostic-only.

use crate::report::RepBreakdown;
use serde::de::{self, MapAccess, Visitor};
use serde::{Deserialize, Deserializer};
use serde_json::{json, Map, Number, Value};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::{CStr, CString};
use std::fs::File;
use std::io::Read;
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::ffi::OsStrExt;
use std::path::Path;

pub const MAX_PROCESSES: usize = 256;
pub const MAX_FILE_BYTES: usize = 1 << 20;
pub const MAX_TOTAL_BYTES: usize = 16 << 20;
pub const MAX_EVENTS: usize = 4096;
pub const MAX_LINE_BYTES: usize = 3072;
const FIELDS: [&str; 5] = [
    "instructions",
    "rep_bytes",
    "rep_entries",
    "unsupported_rep_entries",
    "signals_observed",
];
const REP_BREAKDOWN_FIELDS: [&str; 4] = [
    "rep_movs_bytes",
    "rep_stos_bytes",
    "rep_movs_entries",
    "rep_stos_entries",
];
type Error = &'static str;
type Event = Map<String, Value>;
type Streams = Vec<(u32, Vec<Event>)>;

/// Return a complete aggregate, or an explicitly unavailable result with no
/// counter totals. No Python process or runtime is used by this function.
pub fn collect(directory: &Path, root_pid: u32) -> Value {
    let result = if valid_pid(root_pid.into()) {
        load_records(directory).and_then(|streams| aggregate(streams, root_pid))
    } else {
        Err("invalid root PID")
    };
    result.unwrap_or_else(|reason| invalid(root_pid, reason))
}

fn valid_pid(pid: u64) -> bool {
    pid > 0 && pid <= i32::MAX as u64
}

fn require(condition: bool, reason: Error) -> Result<(), Error> {
    if condition {
        Ok(())
    } else {
        Err(reason)
    }
}

fn open_directory(path: &Path) -> Result<File, Error> {
    let path = CString::new(path.as_os_str().as_bytes()).map_err(|_| "invalid directory path")?;
    // SAFETY: the CString is NUL terminated and lives through open. A successful
    // descriptor is transferred exactly once to File for close-on-drop.
    let fd = unsafe {
        libc::open(
            path.as_ptr(),
            libc::O_RDONLY | libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC,
        )
    };
    if fd < 0 {
        return Err("cannot open record directory");
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

struct DirectoryStream(*mut libc::DIR);
impl Drop for DirectoryStream {
    fn drop(&mut self) {
        // SAFETY: fdopendir created this unique non-null DIR, only this owner
        // closes it, and no dirent pointer is retained after an iteration.
        unsafe {
            libc::closedir(self.0);
        }
    }
}

fn directory_names(fd: RawFd) -> Result<Vec<Vec<u8>>, Error> {
    // SAFETY: duplicate the live directory FD; fdopendir owns only the duplicate.
    let duplicate = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 0) };
    if duplicate < 0 {
        return Err("cannot enumerate record directory");
    }
    let pointer = unsafe { libc::fdopendir(duplicate) };
    if pointer.is_null() {
        unsafe {
            libc::close(duplicate);
        }
        return Err("cannot enumerate record directory");
    }
    let stream = DirectoryStream(pointer);
    let mut names = Vec::new();
    loop {
        // SAFETY: readdir returns a NUL-terminated name until the next call.
        // errno belongs to this thread and is inspected immediately on EOF.
        let (entry, error) = unsafe {
            *libc::__errno_location() = 0;
            let entry = libc::readdir(stream.0);
            (entry, *libc::__errno_location())
        };
        if entry.is_null() {
            require(error == 0, "record directory enumeration failed")?;
            break;
        }
        let name = unsafe { CStr::from_ptr((*entry).d_name.as_ptr()) }.to_bytes();
        if name == b"." || name == b".." {
            continue;
        }
        names.push(name.to_vec());
        require(
            names.len() <= MAX_PROCESSES,
            "process file count outside bound",
        )?;
    }
    require(!names.is_empty(), "process file count outside bound")?;
    names.sort();
    require(
        names.windows(2).all(|pair| pair[0] != pair[1]),
        "record directory changed during enumeration",
    )?;
    Ok(names)
}

fn filename_pid(name: &[u8]) -> Result<u32, Error> {
    let text = std::str::from_utf8(name).map_err(|_| "unexpected report entry")?;
    let raw = text
        .strip_prefix("process-")
        .and_then(|s| s.strip_suffix(".jsonl"))
        .ok_or("unexpected report entry")?;
    require(
        !raw.is_empty() && !raw.starts_with('0') && raw.bytes().all(|c| c.is_ascii_digit()),
        "unexpected report entry",
    )?;
    let pid: u32 = raw.parse().map_err(|_| "invalid filename PID")?;
    require(valid_pid(pid.into()), "invalid filename PID")?;
    Ok(pid)
}

fn file_stat(fd: RawFd) -> Result<libc::stat, Error> {
    // SAFETY: fstat initializes the complete stat object on success.
    let mut st = std::mem::MaybeUninit::<libc::stat>::uninit();
    if unsafe { libc::fstat(fd, st.as_mut_ptr()) } != 0 {
        return Err("cannot stat report file");
    }
    Ok(unsafe { st.assume_init() })
}

fn same_stat(before: &libc::stat, after: &libc::stat) -> bool {
    before.st_dev == after.st_dev
        && before.st_ino == after.st_ino
        && before.st_size == after.st_size
        && before.st_mtime == after.st_mtime
        && before.st_mtime_nsec == after.st_mtime_nsec
        && before.st_ctime == after.st_ctime
        && before.st_ctime_nsec == after.st_ctime_nsec
        && after.st_nlink == 1
}

fn load_records(directory: &Path) -> Result<Streams, Error> {
    let root = open_directory(directory)?;
    let names = directory_names(root.as_raw_fd())?;
    let mut total_bytes = 0usize;
    let mut streams = Vec::with_capacity(names.len());
    for name in names {
        let pid = filename_pid(&name)?;
        let name = CString::new(name).map_err(|_| "unexpected report entry")?;
        // SAFETY: root FD remains live, name is a single validated leaf, and a
        // successful FD is uniquely owned by File. NONBLOCK prevents FIFO hangs.
        let fd = unsafe {
            libc::openat(
                root.as_raw_fd(),
                name.as_ptr(),
                libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK,
            )
        };
        if fd < 0 {
            return Err("cannot open report file");
        }
        let mut file = unsafe { File::from_raw_fd(fd) };
        let before = file_stat(file.as_raw_fd())?;
        require(
            before.st_mode & libc::S_IFMT == libc::S_IFREG && before.st_nlink == 1,
            "unsafe report file",
        )?;
        require(
            before.st_size > 0 && before.st_size <= MAX_FILE_BYTES as i64,
            "report size outside bound",
        )?;
        total_bytes = total_bytes
            .checked_add(before.st_size as usize)
            .ok_or("total report size exceeded")?;
        require(total_bytes <= MAX_TOTAL_BYTES, "total report size exceeded")?;
        let mut data = Vec::with_capacity(before.st_size as usize);
        Read::by_ref(&mut file)
            .take((MAX_FILE_BYTES + 1) as u64)
            .read_to_end(&mut data)
            .map_err(|_| "cannot read report file")?;
        require(
            data.len() == before.st_size as usize && data.last() == Some(&b'\n'),
            "incomplete or growing report",
        )?;
        require(
            same_stat(&before, &file_stat(file.as_raw_fd())?),
            "report changed during collection",
        )?;
        streams.push((pid, decode_stream(pid, &data)?));
    }
    Ok(streams)
}

/// Collect bounded, caller-supplied process-stream bytes without filesystem I/O.
/// This uses the same decoder and lifecycle validator as [`collect`]. It checks
/// every stream boundary before parsing and never exposes partial totals.
pub fn collect_bytes(streams: &[(u32, &[u8])], root_pid: u32) -> Value {
    let result = (|| {
        require(valid_pid(root_pid.into()), "invalid root PID")?;
        require(
            !streams.is_empty() && streams.len() <= MAX_PROCESSES,
            "process file count outside bound",
        )?;
        let mut seen = BTreeSet::new();
        let mut total = 0usize;
        let mut records = Vec::with_capacity(streams.len());
        for &(pid, data) in streams {
            require(
                valid_pid(pid.into()) && seen.insert(pid),
                "duplicate or invalid stream PID",
            )?;
            total = total
                .checked_add(data.len())
                .ok_or("total report size exceeded")?;
            require(total <= MAX_TOTAL_BYTES, "total report size exceeded")?;
            records.push((pid, decode_stream(pid, data)?));
        }
        records.sort_by_key(|(pid, _)| format!("process-{pid}.jsonl"));
        aggregate(records, root_pid)
    })();
    result.unwrap_or_else(|reason| invalid(root_pid, reason))
}

fn invalid(root_pid: u32, reason: Error) -> Value {
    json!({"schema": 1, "authority": "diagnostic_only",
        "integrity_verified": false, "measurement_contract": crate::report::measurement_contract(),
        "rep_telemetry_status": "unavailable",
        "metric": "bb_entry_app_instructions", "scope": "process_tree_user_code",
        "valid": false, "complete": false, "root_pid": root_pid, "reason": reason})
}

fn decode_stream(pid: u32, data: &[u8]) -> Result<Vec<Event>, Error> {
    require(
        !data.is_empty() && data.len() <= MAX_FILE_BYTES,
        "report size outside bound",
    )?;
    require(data.last() == Some(&b'\n'), "incomplete or growing report")?;
    let mut records = Vec::new();
    // Exclude the mandatory final LF; reject CRLF and empty intermediate lines.
    for line in data[..data.len() - 1].split(|byte| *byte == b'\n') {
        require(
            !line.is_empty() && line.len() < MAX_LINE_BYTES && !line.contains(&b'\r'),
            "event size or line ending outside contract",
        )?;
        require(records.len() < MAX_EVENTS, "event count exceeded")?;
        let text = std::str::from_utf8(line).map_err(|_| "invalid report UTF-8")?;
        let record: FlatEvent =
            serde_json::from_str(text).map_err(|_| "invalid or duplicate report JSON field")?;
        validate_field_types(&record.0)?;
        require(uint(&record.0, "pid")? == pid as u64, "record PID mismatch")?;
        records.push(record.0);
    }
    Ok(records)
}

// Deserialize flat objects directly.
// Value's default map implementation would silently overwrite duplicate keys.
struct FlatEvent(Event);
impl<'de> Deserialize<'de> for FlatEvent {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ObjectVisitor;
        impl<'de> Visitor<'de> for ObjectVisitor {
            type Value = FlatEvent;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("a flat event object")
            }
            fn visit_map<M: MapAccess<'de>>(self, mut map: M) -> Result<Self::Value, M::Error> {
                let mut fields = Map::new();
                while let Some(key) = map.next_key::<String>()? {
                    if fields.contains_key(&key) {
                        return Err(de::Error::custom("duplicate key"));
                    }
                    fields.insert(key, map.next_value::<Scalar>()?.0);
                }
                Ok(FlatEvent(fields))
            }
        }
        deserializer.deserialize_map(ObjectVisitor)
    }
}
struct Scalar(Value);
impl<'de> Deserialize<'de> for Scalar {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> Result<Self, D::Error> {
        struct ScalarVisitor;
        impl Visitor<'_> for ScalarVisitor {
            type Value = Scalar;
            fn expecting(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                f.write_str("a JSON scalar")
            }
            fn visit_bool<E: de::Error>(self, v: bool) -> Result<Scalar, E> {
                Ok(Scalar(Value::Bool(v)))
            }
            fn visit_u64<E: de::Error>(self, v: u64) -> Result<Scalar, E> {
                Ok(Scalar(Value::Number(v.into())))
            }
            fn visit_i64<E: de::Error>(self, v: i64) -> Result<Scalar, E> {
                Ok(Scalar(Value::Number(v.into())))
            }
            fn visit_f64<E: de::Error>(self, v: f64) -> Result<Scalar, E> {
                Number::from_f64(v)
                    .map(|n| Scalar(Value::Number(n)))
                    .ok_or_else(|| de::Error::custom("nonfinite number"))
            }
            fn visit_str<E: de::Error>(self, v: &str) -> Result<Scalar, E> {
                Ok(Scalar(Value::String(v.into())))
            }
            fn visit_string<E: de::Error>(self, v: String) -> Result<Scalar, E> {
                Ok(Scalar(Value::String(v)))
            }
            fn visit_unit<E: de::Error>(self) -> Result<Scalar, E> {
                Ok(Scalar(Value::Null))
            }
        }
        deserializer.deserialize_any(ScalarVisitor)
    }
}

fn uint(event: &Event, field: &str) -> Result<u64, Error> {
    event
        .get(field)
        .and_then(Value::as_u64)
        .ok_or("missing or invalid unsigned integer field")
}
fn int(event: &Event, field: &str) -> Result<i64, Error> {
    event
        .get(field)
        .and_then(Value::as_i64)
        .ok_or("missing or invalid signed integer field")
}
fn text<'a>(event: &'a Event, field: &str) -> Result<&'a str, Error> {
    event
        .get(field)
        .and_then(Value::as_str)
        .ok_or("missing or invalid string field")
}
fn boolean(event: &Event, field: &str) -> Result<bool, Error> {
    event
        .get(field)
        .and_then(Value::as_bool)
        .ok_or("missing or invalid boolean field")
}
fn validate_field_types(event: &Event) -> Result<(), Error> {
    if event.contains_key("cpu_policy_version") || event.contains_key("cpu_policy") {
        require(text(event, "event")? == "start", "CPU policy outside start")?;
        cpu_policy(event)?;
    }
    if event.contains_key("cpuid_profile_version") || event.contains_key("cpuid_profile") {
        require(
            text(event, "event")? == "start",
            "CPUID profile outside start",
        )?;
        cpuid_profile(event)?;
    }
    if event.contains_key("feature") {
        require(
            text(event, "event")? == "cpu_feature_violation",
            "CPU feature outside violation",
        )?;
        text(event, "feature")?;
    }
    if event.contains_key("rep_breakdown_version") {
        require(
            uint(event, "rep_breakdown_version")? == 1,
            "unsupported REP breakdown version",
        )?;
    }
    if event.contains_key("latency_bound_version") {
        require(
            uint(event, "latency_bound_version")? == 1,
            "unsupported latency bound capability",
        )?;
        require(
            text(event, "event")? == "start",
            "latency bound capability outside start",
        )?;
    }
    if event.contains_key("latency_bound_x16") {
        uint(event, "latency_bound_x16")?;
        require(
            matches!(text(event, "event")?, "end" | "exec_attempt"),
            "latency bound outside terminal",
        )?;
    }
    for field in REP_BREAKDOWN_FIELDS {
        if event.contains_key(field) {
            uint(event, field)?;
        }
    }
    for field in [
        "schema",
        "client_version",
        "pid",
        "seq",
        "parent_pid",
        "child_pid",
        "syscall",
        "instructions",
        "rep_bytes",
        "rep_entries",
        "unsupported_rep_entries",
        "signals_observed",
        "threads_started",
        "threads_finished",
        "app_exit_code",
    ] {
        if event.contains_key(field) {
            uint(event, field)?;
        }
    }
    for field in [
        "authority",
        "metric",
        "segment",
        "event",
        "origin",
        "parent_segment",
        "sdk_version",
        "quality",
    ] {
        if event.contains_key(field) {
            text(event, field)?;
        }
    }
    for field in [
        "rep_bytes_enabled",
        "supported",
        "valid",
        "accounting_error",
        "threads_complete",
        "app_exit_seen",
    ] {
        if event.contains_key(field) {
            boolean(event, field)?;
        }
    }
    if event.contains_key("result") {
        int(event, "result")?;
    }
    Ok(())
}

fn cpu_policy(event: &Event) -> Result<&str, Error> {
    if !event.contains_key("cpu_policy_version") && !event.contains_key("cpu_policy") {
        return Ok("");
    }
    require(
        uint(event, "cpu_policy_version")? == 1,
        "unsupported CPU policy version",
    )?;
    let policy = text(event, "cpu_policy")?;
    require(
        matches!(policy, "avx" | "simd" | "avx,simd"),
        "invalid CPU policy",
    )?;
    Ok(policy)
}

/// Fixed virtual-CPU identity receipt. Absent means host CPUID was visible.
fn cpuid_profile(event: &Event) -> Result<&str, Error> {
    if !event.contains_key("cpuid_profile_version") && !event.contains_key("cpuid_profile") {
        return Ok("");
    }
    require(
        uint(event, "cpuid_profile_version")? == 1,
        "unsupported CPUID profile version",
    )?;
    let profile = text(event, "cpuid_profile")?;
    require(
        matches!(profile, "x86-64-v1" | "x86-64-v2"),
        "invalid CPUID profile",
    )?;
    Ok(profile)
}

struct Terminal {
    counters: [u64; 5],
    rep_breakdown: Option<RepBreakdown>,
    latency_bound_x16: Option<u64>,
    threads: u64,
    exit_code: Option<u8>,
}
fn terminal(
    event: &Event,
    is_exec: bool,
    rep_enabled: bool,
    breakdown_enabled: bool,
    latency_enabled: bool,
) -> Result<Terminal, Error> {
    let mut counters = [0; 5];
    for (index, field) in FIELDS.iter().enumerate() {
        counters[index] = uint(event, field)?;
    }
    require(
        boolean(event, "valid")? && !boolean(event, "accounting_error")?,
        "invalid terminal accounting",
    )?;
    let threads = uint(event, "threads_started")?;
    require(
        threads > 0
            && threads == uint(event, "threads_finished")?
            && boolean(event, "threads_complete")?,
        "incomplete threads",
    )?;
    require(counters[0] > 0, "zero work")?;
    require(
        text(event, "quality")?
            == if counters[4] == 0 {
                "uninterrupted"
            } else {
                "signals_observed"
            },
        "signal quality mismatch",
    )?;
    require(
        counters[1] == 0 || counters[2] > 0,
        "REP bytes without REP entries",
    )?;
    if !rep_enabled {
        require(counters[1..4] == [0, 0, 0], "REP counters without REP mode")?;
    }
    let rep_breakdown = if breakdown_enabled {
        require(
            REP_BREAKDOWN_FIELDS
                .iter()
                .all(|field| event.contains_key(*field)),
            "missing advertised REP breakdown",
        )?;
        let breakdown = RepBreakdown::from_counts(
            uint(event, "rep_movs_bytes")?,
            uint(event, "rep_stos_bytes")?,
            uint(event, "rep_movs_entries")?,
            uint(event, "rep_stos_entries")?,
        );
        require(
            breakdown.matches_totals(counters[1], counters[2], rep_enabled),
            "REP breakdown partition mismatch",
        )?;
        Some(breakdown)
    } else {
        require(
            REP_BREAKDOWN_FIELDS
                .iter()
                .all(|field| !event.contains_key(*field)),
            "unadvertised REP breakdown",
        )?;
        None
    };
    require(
        event.contains_key("latency_bound_x16") == latency_enabled,
        "latency bound capability mismatch",
    )?;
    let latency_bound_x16 = if latency_enabled {
        let bound = uint(event, "latency_bound_x16")?;
        // Every charged block keeps at least its 4-wide issue cost (4/16 cycle
        // per instruction), including after a proportional replay rollback.
        require(
            counters[0]
                .checked_mul(4)
                .is_some_and(|issue| bound >= issue),
            "latency bound below issue cost",
        )?;
        Some(bound)
    } else {
        None
    };
    let code =
        u8::try_from(uint(event, "app_exit_code")?).map_err(|_| "invalid application exit code")?;
    if !is_exec {
        require(boolean(event, "app_exit_seen")?, "missing application exit")?;
    }
    Ok(Terminal {
        counters,
        rep_breakdown,
        latency_bound_x16,
        threads,
        exit_code: if is_exec { None } else { Some(code) },
    })
}
fn latency_json(cycles_x16: u64) -> Value {
    json!({"version": 1, "cycles_x16": cycles_x16})
}
fn add_counters(target: &mut [u64; 5], source: &[u64; 5]) -> Result<(), Error> {
    for (a, b) in target.iter_mut().zip(source) {
        *a = a.checked_add(*b).ok_or("tree counter overflow")?;
    }
    Ok(())
}
fn insert_counters(value: &mut Value, counters: &[u64; 5]) {
    for (field, count) in FIELDS.iter().zip(counters) {
        value[*field] = json!(count);
    }
}

fn aggregate(streams: Streams, root_pid: u32) -> Result<Value, Error> {
    require(
        streams.iter().any(|(pid, _)| *pid == root_pid),
        "root PID absent",
    )?;
    // A violating process may have no final image. Check every advertised
    // policy first so an early violation cannot hide a mixed-policy tree.
    let mut policy = None;
    let mut cpuid = None;
    for (_, records) in &streams {
        for record in records {
            validate_field_types(record)?;
            if text(record, "event")? == "start" {
                let current = cpu_policy(record)?;
                require(
                    policy.is_none_or(|previous| previous == current),
                    "mixed CPU policies",
                )?;
                policy = Some(current);
                // Every image must observe the same virtual processor; a
                // host-visible image would reintroduce CPU-dependent paths.
                let current = cpuid_profile(record)?;
                require(
                    cpuid.is_none_or(|previous| previous == current),
                    "mixed CPUID profiles",
                )?;
                cpuid = Some(current);
            }
        }
    }
    let policy = policy.unwrap_or("").to_owned();
    let cpuid = cpuid.unwrap_or("").to_owned();
    let mut seen_segments = BTreeSet::new();
    let mut processes = BTreeMap::new();
    let mut spawn_edges = BTreeMap::<u32, (u32, String)>::new();
    let mut rep_mode = None;
    let mut breakdown_mode = None;
    let mut latency_mode = None;
    let mut latency_total = 0u64;
    let mut totals = [0u64; 5];
    let mut breakdown_totals = RepBreakdown::default();
    let mut all_segments = Vec::new();
    for (process, records) in streams {
        let mut state: Option<String> = None;
        let mut first_origin: Option<(u32, String)> = None;
        let mut pending_exec: Option<Event> = None;
        let mut sequence = 0u64;
        let mut ended = false;
        let mut selected: Vec<(String, Terminal)> = Vec::new();
        for record in records {
            require(
                uint(&record, "schema")? == 1
                    && uint(&record, "client_version")? == 1
                    && text(&record, "authority")? == "diagnostic_only"
                    && text(&record, "metric")? == "bb_entry_app_instructions",
                "record version or metric mismatch",
            )?;
            require(
                uint(&record, "pid")? == process as u64,
                "record PID mismatch",
            )?;
            let segment = text(&record, "segment")?.to_owned();
            let prefix = format!("{process}-");
            let suffix = segment.strip_prefix(&prefix).ok_or("invalid segment ID")?;
            require(
                !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_digit()),
                "invalid segment ID",
            )?;
            let kind = text(&record, "event")?;
            if kind == "start" {
                require(
                    !ended && !seen_segments.contains(&segment),
                    "duplicate segment or reused PID",
                )?;
                require(uint(&record, "seq")? == 1, "start sequence must be one")?;
                let mode = boolean(&record, "rep_bytes_enabled")?;
                require(
                    text(&record, "sdk_version")? == "11.3.0",
                    "start metadata mismatch",
                )?;
                require(
                    rep_mode.is_none_or(|previous| previous == mode),
                    "mixed REP modes",
                )?;
                rep_mode = Some(mode);
                let detailed = record.contains_key("rep_breakdown_version");
                require(
                    breakdown_mode.is_none_or(|previous| previous == detailed),
                    "mixed REP breakdown capabilities",
                )?;
                breakdown_mode = Some(detailed);
                let latency = record.contains_key("latency_bound_version");
                require(
                    latency_mode.is_none_or(|previous| previous == latency),
                    "mixed latency bound capabilities",
                )?;
                latency_mode = Some(latency);
                let parent = uint(&record, "parent_pid")?;
                let parent_segment = text(&record, "parent_segment")?;
                let origin = text(&record, "origin")?;
                if let Some(previous_segment) = state.as_ref() {
                    let checkpoint = pending_exec.take().ok_or("exec image without checkpoint")?;
                    require(
                        origin == "initial" && parent == 0 && parent_segment.is_empty(),
                        "invalid exec origin",
                    )?;
                    selected.push((
                        previous_segment.clone(),
                        terminal(&checkpoint, true, mode, detailed, latency)?,
                    ));
                } else {
                    if process == root_pid {
                        require(
                            origin == "initial" && parent == 0 && parent_segment.is_empty(),
                            "invalid root origin",
                        )?;
                    } else {
                        require(
                            origin == "fork" && valid_pid(parent),
                            "unlinked process origin",
                        )?;
                    }
                    first_origin = Some((parent as u32, parent_segment.to_owned()));
                }
                seen_segments.insert(segment.clone());
                state = Some(segment);
                sequence = 1;
                pending_exec = None;
                continue;
            }
            require(
                !ended && state.as_deref() == Some(segment.as_str()),
                "event outside live segment",
            )?;
            sequence = sequence.checked_add(1).ok_or("event sequence overflow")?;
            require(
                uint(&record, "seq")? == sequence,
                "event sequence gap or duplicate",
            )?;
            match kind {
                "cpu_feature_violation" => {
                    let feature = text(&record, "feature")?;
                    require(
                        matches!(feature, "avx" | "simd")
                            && policy.split(',').any(|enabled| enabled == feature),
                        "CPU violation outside requested policy",
                    )?;
                    return Err(if feature == "avx" {
                        "disallowed CPU feature: avx"
                    } else {
                        "disallowed CPU feature: simd"
                    });
                }
                "spawn" => {
                    let child = uint(&record, "child_pid")?;
                    require(
                        valid_pid(child)
                            && child != process as u64
                            && !spawn_edges.contains_key(&(child as u32)),
                        "duplicate or invalid child edge",
                    )?;
                    require(
                        boolean(&record, "supported")?,
                        "unsupported process creation",
                    )?;
                    require(
                        matches!(uint(&record, "syscall")?, 56 | 57 | 58 | 435),
                        "invalid spawn syscall",
                    )?;
                    spawn_edges.insert(child as u32, (process, segment));
                }
                "exec_attempt" => {
                    require(pending_exec.is_none(), "concurrent or missing exec result")?;
                    pending_exec = Some(record);
                }
                "exec_failed" => {
                    require(
                        pending_exec.is_some() && int(&record, "result")? < 0,
                        "invalid failed exec",
                    )?;
                    require(
                        matches!(uint(&record, "syscall")?, 59 | 322),
                        "invalid exec syscall",
                    )?;
                    pending_exec = None;
                }
                "end" => {
                    require(pending_exec.is_none(), "exec outcome missing")?;
                    selected.push((
                        segment,
                        terminal(
                            &record,
                            false,
                            rep_mode.ok_or("missing REP mode")?,
                            breakdown_mode.ok_or("missing REP breakdown capability")?,
                            latency_mode.ok_or("missing latency bound capability")?,
                        )?,
                    ));
                    ended = true;
                }
                _ => return Err("unsupported or interrupted lifecycle event"),
            }
        }
        require(
            ended && first_origin.is_some(),
            "process has no completed final image",
        )?;
        let (parent_pid, parent_segment) = first_origin.ok_or("missing process origin")?;
        let mut process_totals = [0u64; 5];
        let mut process_breakdown = RepBreakdown::default();
        let mut process_latency = 0u64;
        let exit_code = selected.last().ok_or("missing terminal image")?.1.exit_code;
        let images = selected.len();
        for (segment, values) in selected {
            add_counters(&mut totals, &values.counters)?;
            add_counters(&mut process_totals, &values.counters)?;
            let mut row = json!({"pid": process, "segment": segment, "threads": values.threads, "exit_code": values.exit_code});
            insert_counters(&mut row, &values.counters);
            if let Some(breakdown) = values.rep_breakdown {
                breakdown_totals = breakdown_totals
                    .checked_add(breakdown)
                    .ok_or("tree REP breakdown overflow")?;
                process_breakdown = process_breakdown
                    .checked_add(breakdown)
                    .ok_or("process REP breakdown overflow")?;
                row["rep_breakdown"] = json!(breakdown);
            }
            if let Some(bound) = values.latency_bound_x16 {
                latency_total = latency_total
                    .checked_add(bound)
                    .ok_or("latency bound aggregate overflow")?;
                process_latency = process_latency
                    .checked_add(bound)
                    .ok_or("latency bound aggregate overflow")?;
                row["latency_bound"] = latency_json(bound);
            }
            all_segments.push(row);
        }
        let mut row = json!({"pid": process, "parent_pid": parent_pid, "parent_segment": parent_segment, "images": images, "exit_code": exit_code});
        insert_counters(&mut row, &process_totals);
        if breakdown_mode == Some(true) {
            row["rep_breakdown"] = json!(process_breakdown);
        }
        if latency_mode == Some(true) {
            row["latency_bound"] = latency_json(process_latency);
        }
        processes.insert(process, row);
    }
    let actual_children: BTreeSet<_> = processes
        .keys()
        .copied()
        .filter(|pid| *pid != root_pid)
        .collect();
    require(
        spawn_edges.keys().copied().collect::<BTreeSet<_>>() == actual_children,
        "missing or unannounced child process",
    )?;
    for (child, (parent, parent_segment)) in &spawn_edges {
        let row = processes.get(child).ok_or("missing child process")?;
        require(
            processes.contains_key(parent)
                && row["parent_pid"].as_u64() == Some(*parent as u64)
                && row["parent_segment"].as_str() == Some(parent_segment.as_str()),
            "child lineage mismatch",
        )?;
    }
    let mut visited = BTreeSet::from([root_pid]);
    for _ in 0..processes.len() {
        for (child, (parent, _)) in &spawn_edges {
            if visited.contains(parent) {
                visited.insert(*child);
            }
        }
    }
    require(
        visited == processes.keys().copied().collect(),
        "disconnected process graph",
    )?;
    let mut result = json!({
        "schema": 1, "authority": "diagnostic_only", "metric": "bb_entry_app_instructions",
        "integrity_verified": false, "measurement_contract": crate::report::measurement_contract(),
        "scope": "process_tree_user_code", "valid": true, "complete": true,
        "root_pid": root_pid, "process_count": processes.len(), "image_count": all_segments.len(),
        "rep_bytes_enabled": rep_mode.ok_or("missing REP mode")?,
        "rep_telemetry_status": crate::report::rep_telemetry_status(rep_mode.ok_or("missing REP mode")?, totals[3]),
        "quality": if totals[4] == 0 { "uninterrupted" } else { "signals_observed" },
        "processes": processes.into_values().collect::<Vec<_>>(), "segments": all_segments,
        "limits": {"max_processes": MAX_PROCESSES, "max_file_bytes": MAX_FILE_BYTES,
                   "max_total_bytes": MAX_TOTAL_BYTES, "max_events_per_process": MAX_EVENTS},
    });
    insert_counters(&mut result, &totals);
    if breakdown_mode == Some(true) {
        result["rep_breakdown"] = json!(breakdown_totals);
    }
    if latency_mode == Some(true) {
        result["latency_bound"] = latency_json(latency_total);
    }
    if !policy.is_empty() {
        result["disallowed_cpu_features"] = json!(policy.split(',').collect::<Vec<_>>());
    }
    if !cpuid.is_empty() {
        result["cpuid_profile"] = json!(cpuid);
    }
    Ok(result)
}
