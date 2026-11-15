#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
use serde_json::{json, Value};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

const ENGINE: &str = r#"#!/bin/sh
set -eu
out=''
policy=''
while [ "$#" -gt 0 ]; do
 case "$1" in
 -outdir) out="$2"; shift 2 ;;
 -disallow_cpu_feature) policy="${policy:+$policy,}$2"; shift 2 ;;
 --) shift; break ;;
 *) shift ;;
 esac
done
fields=''
if [ -n "$policy" ] && [ "${OLD_CLIENT:-0}" != 1 ]; then
 fields=",\"cpu_policy_version\":1,\"cpu_policy\":\"$policy\""
fi
cat > "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":1,"event":"start","origin":"initial","parent_pid":0,"parent_segment":"","rep_bytes_enabled":true,"sdk_version":"11.3.0"$fields}
JSON
if [ "${VIOLATION:-0}" = 1 ]; then
 cat >> "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"cpu_feature_violation","feature":"avx"}
JSON
 exit 126
fi
cat >> "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"end","instructions":10,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,"valid":true,"quality":"uninterrupted","app_exit_seen":true,"app_exit_code":126}
JSON
exit 126
"#;

#[test]
fn native_policy_roundtrip_preserves_counts_and_normal_exit126() {
    for features in [vec!["avx"], vec!["simd"], vec!["simd", "avx"]] {
        let (status, report) = run(&features, false, false);
        assert_eq!(status, 0);
        let mut expected = features;
        expected.sort_unstable();
        assert_eq!(report["disallowed_cpu_features"], json!(expected));
        assert_eq!(
            report["measurement"]["disallowed_cpu_features"],
            json!(expected)
        );
        assert_eq!(report["execution"]["exit_code"], 126);
        assert_eq!(report["measurement"]["valid"], true);
        assert_eq!(report["measurement"]["instructions"], 10);
        assert_eq!(
            report["measurement"]["measurement_contract"]["backend"],
            "dynamorio"
        );
    }
}

#[test]
fn legacy_client_receipt_cannot_satisfy_enabled_policy() {
    let (status, report) = run(&["avx"], true, false);
    assert_eq!(status, 1);
    assert_eq!(report["measurement"]["valid"], false);
    assert!(report["measurement"].get("instructions").is_none());
    assert_eq!(
        report["measurement"]["reason"],
        "client CPU feature policy does not match the request"
    );
    let (status, report) = run(&[], true, false);
    assert_eq!(status, 0);
    assert!(report.get("disallowed_cpu_features").is_none());
}

#[test]
fn native_policy_violation_never_publishes_partial_work() {
    let (status, report) = run(&["avx"], false, true);
    assert_eq!(status, 1);
    assert_eq!(report["execution"]["exit_code"], 126);
    assert_eq!(report["measurement"]["valid"], false);
    assert_eq!(
        report["measurement"]["reason"],
        "disallowed CPU feature: avx"
    );
    assert!(report["measurement"].get("instructions").is_none());
}

#[test]
fn invalid_policy_arguments_fail_before_launch_or_output_creation() {
    for subcommand in ["run", "isolate"] {
        for features in [
            vec!["AVX"],
            vec!["avx2"],
            vec!["avx", "avx"],
            vec!["simd", "simd"],
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
            for feature in features {
                command.args(["--disallow-cpu-feature", feature]);
            }
            let result = command.args(["--", "/missing"]).output().unwrap();
            assert_eq!(result.status.code(), Some(2));
            assert!(!output.exists());
        }
    }
}

fn run(features: &[&str], old: bool, violation: bool) -> (i32, Value) {
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
        .env("VIOLATION", if violation { "1" } else { "0" });
    for feature in features {
        command.args(["--disallow-cpu-feature", feature]);
    }
    let output = command.args(["--", "/ignored-target"]).output().unwrap();
    assert!(output.stdout.is_empty(), "{output:?}");
    let result = serde_json::from_slice(&fs::read(report).unwrap()).unwrap();
    (output.status.code().unwrap(), result)
}
