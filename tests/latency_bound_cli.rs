#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
//! `--latency-bound` plumbing with a mock engine: the CLI forwards the option
//! and accepts a complete count only with exactly the requested bound.
use serde_json::{json, Value};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

const ENGINE: &str = r#"#!/bin/sh
set -eu
out=''
latency=0
while [ "$#" -gt 0 ]; do
 case "$1" in
 -outdir) out="$2"; shift 2 ;;
 -latency_bound) latency=1; shift ;;
 --) shift; break ;;
 *) shift ;;
 esac
done
if [ "${REPORT_LATENCY:-}" = always ]; then latency=1; fi
if [ "${REPORT_LATENCY:-}" = never ]; then latency=0; fi
start=''
terminal=''
if [ "$latency" = 1 ]; then
 start=',"latency_bound_version":1'
 terminal=",\"latency_bound_x16\":${BOUND:-96}"
fi
cat > "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":1,"event":"start","origin":"initial","parent_pid":0,"parent_segment":"","rep_bytes_enabled":true,"sdk_version":"11.3.0"$start}
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"end","instructions":10,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,"valid":true,"quality":"uninterrupted","app_exit_seen":true,"app_exit_code":0$terminal}
JSON
"#;

fn run(requested: bool, report_latency: &str, bound: u64) -> (i32, Value) {
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
        .env("REPORT_LATENCY", report_latency)
        .env("BOUND", bound.to_string());
    if requested {
        command.arg("--latency-bound");
    }
    let output = command.args(["--", "/ignored-target"]).output().unwrap();
    assert!(output.stdout.is_empty(), "{output:?}");
    let result = serde_json::from_slice(&fs::read(report).unwrap()).unwrap();
    (output.status.code().unwrap(), result)
}

#[test]
fn requested_bound_roundtrips_without_changing_counts() {
    let (status, report) = run(true, "", 96);
    assert_eq!(status, 0, "{report}");
    assert_eq!(report["latency_bound_version"], 1);
    let measurement = &report["measurement"];
    assert_eq!(measurement["valid"], true);
    assert_eq!(measurement["instructions"], 10);
    let bound = json!({"version": 1, "cycles_x16": 96});
    assert_eq!(measurement["latency_bound"], bound);
    assert_eq!(measurement["processes"][0]["latency_bound"], bound);
    assert_eq!(measurement["segments"][0]["latency_bound"], bound);
    let (status, report) = run(false, "", 96);
    assert_eq!(status, 0, "{report}");
    assert!(report.get("latency_bound_version").is_none());
    assert!(report["measurement"].get("latency_bound").is_none());
    assert_eq!(report["measurement"]["instructions"], 10);
}

#[test]
fn missing_unrequested_or_impossible_bound_never_publishes_counts() {
    for (requested, report_latency, bound, reason) in [
        (
            true,
            "never",
            96,
            "client latency bound does not match the request",
        ),
        (
            false,
            "always",
            96,
            "client latency bound does not match the request",
        ),
        (true, "", 39, "latency bound below issue cost"),
    ] {
        let (status, report) = run(requested, report_latency, bound);
        assert_eq!(status, 1, "{report}");
        let measurement = &report["measurement"];
        assert_eq!(measurement["valid"], false);
        assert!(measurement.get("instructions").is_none());
        assert!(measurement.get("latency_bound").is_none());
        assert_eq!(measurement["reason"], reason, "{report}");
    }
}
