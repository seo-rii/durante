//! Bounded collection of QEMU guest instruction callback counts.
//!
//! The caller must first reap the emulator and validate its independent CPU
//! policy, sandbox, and resource outcome. A completion record is neither a
//! trusted verdict nor evidence that these external checks succeeded.

use serde::{Deserialize, Deserializer};
use serde_json::{json, Value};
use std::fs::{Metadata, OpenOptions};
use std::io::Read;
use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
use std::path::Path;

pub const MAX_REPORT_BYTES: usize = 1024;
type Error = &'static str;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Record {
    schema: u64,
    kind: String,
    pid: u32,
    complete: bool,
    #[serde(default, deserialize_with = "present")]
    instructions: Option<u64>,
    #[serde(default, deserialize_with = "present")]
    exit_code: Option<u8>,
    #[serde(default, deserialize_with = "present")]
    reason: Option<String>,
}

// Absence is permitted; a present null is not. This also keeps derive's
// duplicate-field detection effective for every optional field.
fn present<'de, D, T>(deserializer: D) -> Result<Option<T>, D::Error>
where
    D: Deserializer<'de>,
    T: Deserialize<'de>,
{
    T::deserialize(deserializer).map(Some)
}

/// Distinct from DynamoRIO basic-block-entry work and from retired instructions.
pub fn measurement_contract() -> Value {
    json!({
        "version": 1,
        "backend": "qemu_tcg",
        "counter_integrity": "emulator_managed",
        "trusted_verdict_eligible": false,
        "instruction_semantics": "guest_instruction_callback",
        "simd_weighting": "one_per_callback",
        "cpu_time_conversion": null
    })
}

fn envelope(root_pid: u32) -> Value {
    json!({
        "schema": 1,
        "authority": "diagnostic_only",
        "metric": "guest_instruction_callbacks",
        "scope": "single_process_guest_user_code",
        "integrity_verified": false,
        "measurement_contract": measurement_contract(),
        "rep_telemetry_status": "unavailable",
        "root_pid": root_pid,
        "valid": false,
        "complete": false
    })
}

/// Collect one closed QEMU completion record, matching the PID and normal exit
/// code observed by the caller. Never call this after forced termination or
/// failed CPU-policy/resource checks, even if a record happens to be present.
pub fn collect(input: &Path, root_pid: u32, exit_code: u8) -> Value {
    let mut result = envelope(root_pid);
    let accepted = if root_pid == 0 || root_pid > i32::MAX as u32 {
        Err("invalid root PID")
    } else {
        load(input).and_then(|data| decode(&data, root_pid, exit_code))
    };
    match accepted {
        Ok(instructions) => {
            result["valid"] = json!(true);
            result["complete"] = json!(true);
            result["instructions"] = json!(instructions);
            result["exit_code"] = json!(exit_code);
        }
        Err(reason) => result["reason"] = json!(reason),
    }
    result
}

fn same_file(before: &Metadata, after: &Metadata) -> bool {
    before.dev() == after.dev()
        && before.ino() == after.ino()
        && before.len() == after.len()
        && before.mode() == after.mode()
        && before.uid() == after.uid()
        && before.gid() == after.gid()
        && before.mtime() == after.mtime()
        && before.mtime_nsec() == after.mtime_nsec()
        && before.ctime() == after.ctime()
        && before.ctime_nsec() == after.ctime_nsec()
        && after.nlink() == 1
}

fn load(input: &Path) -> Result<Vec<u8>, Error> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(input)
        .map_err(|_| "cannot open QEMU report")?;
    let before = file.metadata().map_err(|_| "cannot stat QEMU report")?;
    if !before.is_file() || before.nlink() != 1 {
        return Err("unsafe QEMU report file");
    }
    if before.len() == 0 || before.len() > MAX_REPORT_BYTES as u64 {
        return Err("QEMU report size outside bound");
    }
    let mut bytes = Vec::with_capacity(before.len() as usize);
    (&file)
        .take((MAX_REPORT_BYTES + 1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|_| "cannot read QEMU report")?;
    let after = file.metadata().map_err(|_| "cannot stat QEMU report")?;
    if bytes.len() != before.len() as usize || !same_file(&before, &after) {
        return Err("QEMU report changed during collection");
    }
    Ok(bytes)
}

fn decode(data: &[u8], root_pid: u32, exit_code: u8) -> Result<u64, Error> {
    if data.len() < 3
        || data.len() > MAX_REPORT_BYTES
        || data.first() != Some(&b'{')
        || !data.ends_with(b"}\n")
        || data[..data.len() - 1].contains(&b'\n')
        || data.contains(&b'\r')
    {
        return Err("QEMU report must contain one complete JSON line");
    }
    let record: Record = serde_json::from_slice(&data[..data.len() - 1])
        .map_err(|_| "invalid QEMU report fields")?;
    if record.schema != 1 || record.kind != "durante-qemu-work" || record.pid != root_pid {
        return Err("QEMU report identity mismatch");
    }
    if !record.complete {
        if record.instructions.is_some()
            || record.exit_code.is_some()
            || !record.reason.as_ref().is_some_and(|reason| {
                !reason.is_empty()
                    && reason.len() <= 128
                    && reason.bytes().all(|byte| (0x20..=0x7e).contains(&byte))
            })
        {
            return Err("invalid incomplete QEMU report");
        }
        // Do not echo a possibly sensitive or forged input diagnostic.
        return Err("QEMU guest measurement did not complete");
    }
    if record.reason.is_some()
        || record.exit_code != Some(exit_code)
        || record.instructions.is_none_or(|count| count == 0)
    {
        return Err("QEMU completion disagrees with the observed execution");
    }
    Ok(record.instructions.unwrap())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn metadata_comparison_rejects_replaced_or_rewritten_input() {
        let directory = tempfile::tempdir().unwrap();
        let first = directory.path().join("first");
        let second = directory.path().join("second");
        fs::write(&first, b"one").unwrap();
        fs::write(&second, b"one").unwrap();
        let before = fs::metadata(&first).unwrap();
        assert!(same_file(&before, &before));
        assert!(!same_file(&before, &fs::metadata(&second).unwrap()));
        fs::write(&first, b"changed").unwrap();
        assert!(!same_file(&before, &fs::metadata(&first).unwrap()));
    }
}
