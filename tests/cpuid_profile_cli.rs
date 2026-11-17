#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
//! `--cpuid-profile` plumbing with a mock engine: the CLI forwards the profile
//! and accepts a complete count only with the client's exact receipt.
use serde_json::{json, Value};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

const ENGINE: &str = r#"#!/bin/sh
set -eu
out=''
profile=''
while [ "$#" -gt 0 ]; do
 case "$1" in
 -outdir) out="$2"; shift 2 ;;
 -cpuid_profile) profile="$2"; shift 2 ;;
 --) shift; break ;;
 *) shift ;;
 esac
done
if [ -n "${REPORT_PROFILE:-}" ]; then
 profile="$REPORT_PROFILE"
fi
fields=''
if [ -n "$profile" ] && [ "${OLD_CLIENT:-0}" != 1 ]; then
 fields=",\"cpuid_profile_version\":1,\"cpuid_profile\":\"$profile\""
fi
cat > "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":1,"event":"start","origin":"initial","parent_pid":0,"parent_segment":"","rep_bytes_enabled":true,"sdk_version":"11.3.0"$fields}
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"end","instructions":10,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,"valid":true,"quality":"uninterrupted","app_exit_seen":true,"app_exit_code":0}
JSON
"#;

fn run(profile: Option<&str>, old: bool, report_profile: Option<&str>) -> (i32, Value) {
    let dir = tempfile::tempdir().unwrap();
    let engine = dir.path().join("engine");
    let client = dir.path().join("client.so");
    let report = dir.path().join("report.json");
    fs::write(&engine, ENGINE).unwrap();
    fs::set_permissions(&engine, fs::Permissions::from_mode(0o755)).unwrap();
    fs::write(&client, b"mock client").unwrap();
    let mut command = Command::new(env!("CARGO_BIN_EXE_durante"));
    command
        .args(["run", "--drrun"])
        .arg(engine)
        .arg("--client")
        .arg(client)
        .arg("--report")
        .arg(&report)
        .env("OLD_CLIENT", if old { "1" } else { "0" })
        .env("REPORT_PROFILE", report_profile.unwrap_or(""));
    if let Some(profile) = profile {
        command.args(["--cpuid-profile", profile]);
    }
    let output = command.args(["--", "/ignored-target"]).output().unwrap();
    assert!(output.stdout.is_empty(), "{output:?}");
    let result = serde_json::from_slice(&fs::read(report).unwrap()).unwrap();
    (output.status.code().unwrap(), result)
}

#[test]
fn profile_roundtrip_preserves_counts() {
    for profile in ["x86-64-v1", "x86-64-v2"] {
        let (status, report) = run(Some(profile), false, None);
        assert_eq!(status, 0, "{report}");
        assert_eq!(report["cpuid_profile"], json!(profile));
        assert_eq!(report["measurement"]["cpuid_profile"], json!(profile));
        assert_eq!(report["measurement"]["valid"], true);
        assert_eq!(report["measurement"]["instructions"], 10);
    }
    let (status, report) = run(None, false, None);
    assert_eq!(status, 0);
    assert!(report.get("cpuid_profile").is_none());
    assert!(report["measurement"].get("cpuid_profile").is_none());
}

#[test]
fn missing_or_different_receipt_never_publishes_counts() {
    for (requested, old, reported) in [
        (Some("x86-64-v1"), true, None),
        (Some("x86-64-v1"), false, Some("x86-64-v2")),
        (None, false, Some("x86-64-v2")),
    ] {
        let (status, report) = run(requested, old, reported);
        assert_eq!(status, 1, "{report}");
        assert_eq!(report["measurement"]["valid"], false);
        assert!(report["measurement"].get("instructions").is_none());
        assert_eq!(
            report["measurement"]["reason"],
            "client CPUID profile does not match the request"
        );
    }
}

#[test]
fn invalid_profile_arguments_fail_before_launch_or_output_creation() {
    for subcommand in ["run", "isolate"] {
        for profiles in [
            vec!["x86-64-v3"],
            vec!["X86-64-V1"],
            vec![""],
            vec!["x86-64-v1", "x86-64-v2"],
        ] {
            let dir = tempfile::tempdir().unwrap();
            let output = dir.path().join("report");
            let mut command = Command::new(env!("CARGO_BIN_EXE_durante"));
            command
                .args([
                    subcommand, "--drrun", "/missing", "--client", "/missing", "--report",
                ])
                .arg(&output);
            if subcommand == "isolate" {
                command.args(["--launcher", "/missing", "--workspace", "/missing"]);
            }
            for profile in profiles {
                command.args(["--cpuid-profile", profile]);
            }
            let result = command.args(["--", "/missing"]).output().unwrap();
            assert_eq!(result.status.code(), Some(2));
            assert!(!output.exists());
        }
    }
}
