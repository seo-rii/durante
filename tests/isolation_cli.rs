#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::process::Command;

#[test]
fn unavailable_isolation_never_falls_back_to_host_execution() {
    let dir = tempfile::tempdir().unwrap();
    let marker = dir.path().join("must-not-run");
    let launcher = dir.path().join("bwrap");
    let sdk = dir.path().join("sdk/bin64");
    fs::create_dir_all(&sdk).unwrap();
    // These are deliberately unpinned launchers, not real sandbox tools. An
    // accidental fallback would create only this private fixture's marker.
    let script = format!("#!/bin/sh\ntouch '{}'\nexit 0\n", marker.display());
    for path in [&launcher, &sdk.join("drrun")] {
        fs::write(path, &script).unwrap();
        fs::set_permissions(path, fs::Permissions::from_mode(0o700)).unwrap();
    }
    let client = dir.path().join("client.so");
    fs::write(&client, b"fixture").unwrap();
    let workspace = dir.path().join("input");
    fs::create_dir(&workspace).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_durante"))
        .args(["isolate", "--launcher"])
        .arg(&launcher)
        .arg("--drrun")
        .arg(sdk.join("drrun"))
        .arg("--client")
        .arg(&client)
        .arg("--workspace")
        .arg(&workspace)
        .arg("--report")
        .arg(dir.path().join("report.json"))
        .args(["--", "/bin/true"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert!(!marker.exists(), "unpinned tool or target executed");
}

#[test]
fn internal_existing_report_refuses_nonempty_capture_before_launch() {
    let dir = tempfile::tempdir().unwrap();
    let marker = dir.path().join("must-not-run");
    let launcher = dir.path().join("drrun");
    fs::write(
        &launcher,
        format!("#!/bin/sh\ntouch '{}'\n", marker.display()),
    )
    .unwrap();
    fs::set_permissions(&launcher, fs::Permissions::from_mode(0o700)).unwrap();
    let client = dir.path().join("client.so");
    fs::write(&client, b"fixture").unwrap();
    let report = dir.path().join("capture.json");
    fs::write(&report, b"existing content").unwrap();
    fs::set_permissions(&report, fs::Permissions::from_mode(0o600)).unwrap();
    let output = Command::new(env!("CARGO_BIN_EXE_durante"))
        .args(["run", "--report-existing", "--drrun"])
        .arg(&launcher)
        .arg("--client")
        .arg(&client)
        .arg("--report")
        .arg(&report)
        .args(["--", "/bin/true"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert!(!marker.exists());
    assert_eq!(fs::read(report).unwrap(), b"existing content");
}

#[test]
fn output_quota_requires_capture_and_rejects_unbounded_values() {
    for extra in [
        vec!["--output-limit-bytes", "1"],
        vec!["--output-dir", "/unused", "--output-limit-bytes", "0"],
        vec![
            "--output-dir",
            "/unused",
            "--output-limit-bytes",
            "67108865",
        ],
    ] {
        let output = Command::new(env!("CARGO_BIN_EXE_durante"))
            .args([
                "isolate",
                "--launcher",
                "/unused",
                "--drrun",
                "/unused",
                "--client",
                "/unused",
                "--workspace",
                "/unused",
                "--report",
                "/unused",
            ])
            .args(extra)
            .args(["--", "/bin/true"])
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(2), "{output:?}");
    }
}
