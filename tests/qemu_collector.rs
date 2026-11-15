use durante::qemu::{collect, MAX_REPORT_BYTES};
use serde_json::{json, Value};
use std::fs;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::symlink;
use std::path::Path;
use std::process::Command;

fn success() -> String {
    "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":123,\"complete\":true,\"instructions\":7,\"exit_code\":0}\n".to_owned()
}

fn unavailable(result: &Value) {
    assert_eq!(result["valid"], false, "{result}");
    assert_eq!(result["complete"], false, "{result}");
    assert_eq!(result["authority"], "diagnostic_only");
    assert_eq!(result["integrity_verified"], false);
    assert_eq!(result["rep_telemetry_status"], "unavailable");
    assert_eq!(result["measurement_contract"]["backend"], "qemu_tcg");
    assert_eq!(
        result["measurement_contract"]["trusted_verdict_eligible"],
        false
    );
    assert!(result["reason"]
        .as_str()
        .is_some_and(|reason| !reason.is_empty() && reason.len() <= 128));
    for field in [
        "instructions",
        "exit_code",
        "processes",
        "segments",
        "rep_bytes",
        "rep_entries",
        "rep_breakdown",
    ] {
        assert!(result.get(field).is_none(), "partial {field} in {result}");
    }
}

#[test]
fn complete_guest_count_has_distinct_semantics_and_no_invented_rep_telemetry() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    for (count, exit) in [(1_u64, 0_u8), (7, 126), (u64::MAX, 255)] {
        let raw = success()
            .replace("\"instructions\":7", &format!("\"instructions\":{count}"))
            .replace("\"exit_code\":0", &format!("\"exit_code\":{exit}"));
        fs::write(&input, raw).unwrap();
        let result = collect(&input, 123, exit);
        assert_eq!(
            result,
            json!({
                "schema": 1,
                "authority": "diagnostic_only",
                "metric": "guest_instruction_callbacks",
                "scope": "single_process_guest_user_code",
                "integrity_verified": false,
                "measurement_contract": {
                    "version": 1,
                    "backend": "qemu_tcg",
                    "counter_integrity": "emulator_managed",
                    "trusted_verdict_eligible": false,
                    "instruction_semantics": "guest_instruction_callback",
                    "simd_weighting": "one_per_callback",
                    "cpu_time_conversion": null
                },
                "rep_telemetry_status": "unavailable",
                "root_pid": 123,
                "valid": true,
                "complete": true,
                "instructions": count,
                "exit_code": exit
            })
        );
    }
}

#[test]
fn malformed_or_ambiguous_reports_never_expose_counts() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    let good = success();
    let mut cases = vec![
        String::new(),
        "{}\n".to_owned(),
        good.trim_end().to_owned(),
        good.replace('\n', "\r\n"),
        format!("{good}\n"),
        format!("{good}{good}"),
        format!("{good} "),
        format!(" {good}"),
        good.replace("}\n", " } \n"),
        good.replace("\"schema\":1", "\"schema\":2"),
        good.replace("\"schema\":1", "\"schema\":1.0"),
        good.replace("durante-qemu-work", "bb_entry_app_instructions"),
        good.replace("\"pid\":123", "\"pid\":124"),
        good.replace("\"pid\":123", "\"pid\":123.0"),
        good.replace("\"complete\":true", "\"complete\":1"),
        good.replace("\"instructions\":7", "\"instructions\":0"),
        good.replace("\"instructions\":7", "\"instructions\":7.0"),
        good.replace("\"instructions\":7", "\"instructions\":7e0"),
        good.replace("\"instructions\":7", "\"instructions\":true"),
        good.replace("\"instructions\":7", "\"instructions\":\"7\""),
        good.replace("\"instructions\":7", "\"instructions\":null"),
        good.replace("\"instructions\":7", "\"instructions\":-7"),
        good.replace(
            "\"instructions\":7",
            "\"instructions\":18446744073709551616",
        ),
        good.replace("\"exit_code\":0", "\"exit_code\":1"),
        good.replace("\"exit_code\":0", "\"exit_code\":256"),
        good.replace("\"exit_code\":0", "\"exit_code\":-0"),
        good.replace("\"exit_code\":0", "\"exit_code\":0.0"),
        good.replace("\"exit_code\":0", "\"exit_code\":null"),
        good.replace("}\n", ",\"reason\":null}\n"),
        good.replace("}\n", ",\"reason\":\"failure\"}\n"),
        good.replace("}\n", ",\"integrity_verified\":true}\n"),
        good.replace("}\n", ",\"unrecognized\":{\"nested\":1}}\n"),
        good.replace("}\n", ",\"instructions\":8}\n"),
        good.replace("}\n", ",\"exit_code\":0}\n"),
        good.replace("}\n", ",\"schema\":1}\n"),
        good.replace("}\n", ",\"complete\":true}\n"),
        good.replace("}\n", ",\"kind\":\"durante-qemu-work\"}\n"),
        good.replace("}\n", ",\"pid\":123}\n"),
        good.replace("\"instructions\":7,", ""),
        good.replace(",\"exit_code\":0", ""),
    ];
    for required in ["schema", "kind", "pid", "complete"] {
        let mut value: Value = serde_json::from_str(&good).unwrap();
        value.as_object_mut().unwrap().remove(required);
        cases.push(format!("{value}\n"));
    }
    for (index, raw) in cases.iter().enumerate() {
        fs::write(&input, raw).unwrap();
        let result = collect(&input, 123, 0);
        assert_eq!(result["valid"], false, "case {index}: {raw} => {result}");
        unavailable(&result);
    }
    fs::write(&input, b"{\xff}\n").unwrap();
    unavailable(&collect(&input, 123, 0));
}

#[test]
fn incomplete_records_and_forged_failure_totals_are_unavailable() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    let failure = "{\"schema\":1,\"kind\":\"durante-qemu-work\",\"pid\":123,\"complete\":false,\"reason\":\"PRIVATE_INPUT\"}\n";
    for raw in [
        failure.to_owned(),
        failure.replace("}\n", ",\"instructions\":8}\n"),
        failure.replace("}\n", ",\"exit_code\":0}\n"),
        failure.replace("}\n", ",\"reason\":\"second\"}\n"),
        failure.replace("\"PRIVATE_INPUT\"", "null"),
        failure.replace(",\"reason\":\"PRIVATE_INPUT\"", ""),
        failure.replace("PRIVATE_INPUT", ""),
        failure.replace("PRIVATE_INPUT", &"a".repeat(129)),
        failure.replace("PRIVATE_INPUT", "control\\ncharacter"),
    ] {
        fs::write(&input, raw).unwrap();
        let result = collect(&input, 123, 0);
        unavailable(&result);
        assert!(!result.to_string().contains("PRIVATE_INPUT"));
    }
}

#[test]
fn input_bounds_and_external_identity_are_checked() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    let good = success();
    let padded = good.replace(
        '}',
        &format!("{}}}", " ".repeat(MAX_REPORT_BYTES - good.len())),
    );
    assert_eq!(padded.len(), MAX_REPORT_BYTES);
    fs::write(&input, &padded).unwrap();
    assert_eq!(collect(&input, 123, 0)["valid"], true);
    fs::write(&input, padded.replace('}', " }")).unwrap();
    unavailable(&collect(&input, 123, 0));
    fs::write(&input, good).unwrap();
    for pid in [0, 124, i32::MAX as u32 + 1, u32::MAX] {
        unavailable(&collect(&input, pid, 0));
    }
    unavailable(&collect(&input, 123, 1));
}

#[test]
fn links_nonregular_files_and_missing_inputs_are_rejected_without_blocking() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    fs::write(&input, success()).unwrap();
    let link = directory.path().join("link");
    symlink(&input, &link).unwrap();
    unavailable(&collect(&link, 123, 0));
    fs::remove_file(&link).unwrap();
    fs::hard_link(&input, &link).unwrap();
    unavailable(&collect(&input, 123, 0));
    unavailable(&collect(&link, 123, 0));
    unavailable(&collect(directory.path(), 123, 0));
    unavailable(&collect(&directory.path().join("missing"), 123, 0));
    let fifo = directory.path().join("fifo");
    let path = std::ffi::CString::new(fifo.as_os_str().as_bytes()).unwrap();
    // SAFETY: path is a live NUL-terminated pathname inside the temporary directory.
    assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
    unavailable(&collect(&fifo, 123, 0));
}

fn cli(input: &Path, arguments: &[&str]) -> std::process::Output {
    Command::new(env!("CARGO_BIN_EXE_durante"))
        .args(["collect-qemu", "--input"])
        .arg(input)
        .args(arguments)
        .output()
        .unwrap()
}

#[test]
fn cli_exposes_only_complete_matching_guest_counts() {
    let directory = tempfile::tempdir().unwrap();
    let input = directory.path().join("report");
    fs::write(&input, success()).unwrap();
    let output = cli(&input, &["--root-pid", "123", "--exit-code", "0"]);
    assert_eq!(output.status.code(), Some(0));
    assert_eq!(
        serde_json::from_slice::<Value>(&output.stdout).unwrap(),
        collect(&input, 123, 0)
    );
    assert!(output.stderr.is_empty());
    let output = cli(&input, &["--root-pid", "123", "--exit-code", "126"]);
    assert_eq!(output.status.code(), Some(1));
    unavailable(&serde_json::from_slice::<Value>(&output.stdout).unwrap());
    for arguments in [
        vec!["--root-pid", "123"],
        vec!["--root-pid", "0", "--exit-code", "0"],
        vec!["--root-pid", "123", "--exit-code", "256"],
        vec!["--root-pid", "123", "--exit-code", "PRIVATE_INPUT"],
    ] {
        let output = cli(&input, &arguments);
        assert_eq!(output.status.code(), Some(2));
        assert!(output.stdout.is_empty());
        assert!(!String::from_utf8_lossy(&output.stderr).contains("PRIVATE_INPUT"));
    }
}
