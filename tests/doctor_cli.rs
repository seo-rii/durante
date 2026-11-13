#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::Path;
use std::process::{Command, Output};

fn result(output: &Output) -> Value {
    assert!(output.stderr.is_empty(), "{output:?}");
    let value: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(value["command"], "doctor");
    assert_eq!(value["authority"], "diagnostic_only");
    assert_eq!(value["integrity_verified"], false);
    assert_eq!(value["execution_tested"], false);
    assert_eq!(
        value["measurement_contract"]["trusted_verdict_eligible"],
        false
    );
    value
}

fn status<'a>(report: &'a Value, id: &str) -> &'a str {
    report["checks"]
        .as_array()
        .unwrap()
        .iter()
        .find(|check| check["id"] == id)
        .unwrap()["status"]
        .as_str()
        .unwrap()
}

fn command(root: &Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_durante"));
    command
        .args(["doctor", "--drrun"])
        .arg(root.join("sdk/bin64/drrun"))
        .arg("--client")
        .arg(root.join("client.so"));
    command
}

fn fixture(root: &Path) {
    fs::create_dir_all(root.join("sdk/bin64")).unwrap();
    fs::create_dir_all(root.join("sdk/lib64/release")).unwrap();
    // Consistency-only test installation. A matching manifest must never cause
    // doctor to run the launcher or claim actual execution qualification.
    let marker = root.join("must-not-run");
    fs::write(
        root.join("sdk/bin64/drrun"),
        format!("#!/bin/sh\ntouch '{}'\n", marker.display()),
    )
    .unwrap();
    fs::set_permissions(
        root.join("sdk/bin64/drrun"),
        fs::Permissions::from_mode(0o700),
    )
    .unwrap();
    fs::write(root.join("client.so"), b"test client").unwrap();
    fs::write(root.join("sdk/lib64/release/libdynamorio.so"), b"test core").unwrap();
    let mut manifest = json!({"schema":1,"inputs":{
        "source_commit":"6847bb210bea0ca11d1bf47704383d6a075da490",
        "source_sha256":"e2b7d3f1917ff64e644b81efd96a610a3c622f4857c75593fef5d5e581c231f6",
        "patch_sha256":format!("{:x}", Sha256::digest(include_bytes!(
            "../toolkit/patches/dynamorio-vfork-lifetime.patch"
        ))),
        "recipe":1},"artifacts":{
            "lib64/release/libdynamorio.so": format!("{:x}", Sha256::digest(b"test core"))}});
    // Include additional reviewed patch inputs when these branches combine.
    match fs::read(
        Path::new(env!("CARGO_MANIFEST_DIR")).join("toolkit/patches/dynamorio-code-replay.patch"),
    ) {
        Ok(bytes) => {
            manifest["inputs"]["code_replay_patch_sha256"] =
                json!(format!("{:x}", Sha256::digest(bytes)));
        }
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(error) => panic!("cannot read replay patch fixture: {error}"),
    }
    fs::write(
        root.join("sdk/.durante-runtime.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
}

#[test]
fn missing_options_produce_all_diagnostics_without_false_readiness() {
    let output = Command::new(env!("CARGO_BIN_EXE_durante"))
        .args(["doctor", "--profile", "isolate"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    let report = result(&output);
    assert_eq!(report["checks_passed"], false);
    for id in [
        "drrun_installation",
        "client_installation",
        "sdk_manifest",
        "isolation_launcher",
    ] {
        assert_eq!(status(&report, id), "not_checked");
    }
    assert_eq!(status(&report, "namespace_creation_policy"), "not_checked");
    assert!(report["checks"]
        .as_array()
        .unwrap()
        .iter()
        .any(|check| check["id"] == "cgroup_limits"));
}

#[test]
fn manifest_consistency_is_checked_without_executing_installed_files() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    let before = result(&command(dir.path()).output().unwrap());
    assert_eq!(status(&before, "sdk_manifest"), "pass");
    assert_eq!(status(&before, "drrun_installation"), "pass");
    assert_eq!(status(&before, "client_installation"), "pass");
    assert!(!dir.path().join("must-not-run").exists());
    fs::write(
        dir.path().join("sdk/lib64/release/libdynamorio.so"),
        b"changed core",
    )
    .unwrap();
    let output = command(dir.path()).output().unwrap();
    assert_eq!(output.status.code(), Some(1));
    let after = result(&output);
    assert_eq!(status(&after, "sdk_manifest"), "fail");
    assert_eq!(after["checks_passed"], false);
    assert!(!dir.path().join("must-not-run").exists());
}

#[test]
fn unsafe_client_and_unpinned_launcher_are_reported_independently() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path());
    fs::set_permissions(
        dir.path().join("client.so"),
        fs::Permissions::from_mode(0o666),
    )
    .unwrap();
    let output = command(dir.path())
        .args(["--profile", "isolate", "--launcher"])
        .arg(dir.path().join("sdk/bin64/drrun"))
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    let report = result(&output);
    assert_eq!(status(&report, "client_installation"), "fail");
    assert_eq!(status(&report, "isolation_launcher"), "fail");
    assert_eq!(status(&report, "sdk_manifest"), "pass");
    assert_eq!(
        fs::metadata(dir.path().join("client.so"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o666
    );
    assert!(!dir.path().join("must-not-run").exists());
}

#[test]
fn installation_errors_do_not_echo_sensitive_paths() {
    let output = Command::new(env!("CARGO_BIN_EXE_durante"))
        .args([
            "doctor",
            "--drrun",
            "/missing/private-token-123",
            "--client",
            "/missing/private-token-456",
        ])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert!(!String::from_utf8_lossy(&output.stdout).contains("private-token"));
    let report = result(&output);
    assert_eq!(status(&report, "drrun_installation"), "fail");
    assert_eq!(status(&report, "client_installation"), "fail");
}
