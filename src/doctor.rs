//! Read-only prerequisite diagnostics. A pass is not an execution qualification.
use crate::{isolation, runner};
use serde::Serialize;
use serde_json::json;
use std::fs::File;
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};

pub struct Options {
    pub isolation: bool,
    pub drrun: Option<PathBuf>,
    pub client: Option<PathBuf>,
    pub launcher: Option<PathBuf>,
}

#[derive(Serialize)]
struct Check {
    id: &'static str,
    required: bool,
    status: &'static str,
    detail: &'static str,
    remedy: &'static str,
}

fn check(
    id: &'static str,
    required: bool,
    passed: Option<bool>,
    detail: &'static str,
    remedy: &'static str,
) -> Check {
    Check {
        id,
        required,
        status: match passed {
            Some(true) => "pass",
            Some(false) => "fail",
            None => "not_checked",
        },
        detail,
        remedy,
    }
}

fn installation(
    checks: &mut Vec<Check>,
    id: &'static str,
    path: Option<&Path>,
    executable: bool,
    remedy: &'static str,
) -> Option<PathBuf> {
    let resolved = path.and_then(|p| runner::trusted_installation(p, executable).ok());
    checks.push(check(
        id,
        true,
        path.map(|_| resolved.is_some()),
        "Checks the execution command's regular-file, permission and executable requirements.",
        remedy,
    ));
    resolved
}

fn close_range_available() -> bool {
    // Linux descriptors are signed ints: UINT_MAX cannot name a live handle.
    // This validates the exact flag used by run without closing or marking any
    // caller descriptor. Do not substitute the production 3..UINT_MAX range.
    unsafe { libc::syscall(libc::SYS_close_range, u32::MAX, u32::MAX, 4_u32) == 0 }
}

fn kernel_number(path: &str) -> io::Result<u64> {
    let mut value = String::new();
    File::open(path)?.take(129).read_to_string(&mut value)?;
    if value.len() > 128 {
        return Err(io::Error::other("oversized kernel setting"));
    }
    value
        .trim()
        .parse()
        .map_err(|_| io::Error::other("invalid kernel setting"))
}

pub fn run(options: Options) -> Result<i32, String> {
    let mut checks = vec![check(
        "close_range_cloexec",
        true,
        Some(close_range_available()),
        "Probes close_range(CLOSE_RANGE_CLOEXEC) with an empty descriptor range.",
        "Use a kernel and execution policy that permit close_range with CLOSE_RANGE_CLOEXEC.",
    )];
    let drrun = installation(
        &mut checks,
        "drrun_installation",
        options.drrun.as_deref(),
        true,
        "Supply --drrun pointing to the operator-owned prepared SDK bin64/drrun.",
    );
    installation(
        &mut checks,
        "client_installation",
        options.client.as_deref(),
        false,
        "Supply --client pointing to a regular library without group/other write permissions.",
    );
    let sdk_ok = drrun.as_ref().map(|path| {
        path.file_name() == Some(std::ffi::OsStr::new("drrun"))
            && path.parent().and_then(Path::file_name) == Some(std::ffi::OsStr::new("bin64"))
            && path
                .parent()
                .and_then(Path::parent)
                .is_some_and(|root| isolation::verify_sdk(root).is_ok())
    });
    checks.push(check(
        "sdk_manifest",
        true,
        sdk_ok,
        "Checks the prepared SDK layout, pinned provenance and installed release core hash.",
        "Build a fresh SDK with toolkit/fetch_sdk.py and preserve its .durante-runtime.json.",
    ));

    let mut limits = serde_json::Value::Null;
    if options.isolation {
        checks.push(check(
            "isolation_launcher",
            true,
            options
                .launcher
                .as_ref()
                .map(|path| isolation::verify_launcher(path).is_ok()),
            "Checks the pinned launcher manifest, binary hash and protected file permissions.",
            "Supply --launcher and install its matching .durante-isolation.json beside it.",
        ));
        let current = std::env::current_exe().ok();
        installation(
            &mut checks,
            "cli_installation",
            current.as_deref(),
            true,
            "Install the CLI as an operator-owned regular executable without group/other writes.",
        );
        let namespaces = ["user", "pid", "net", "mnt", "ipc", "uts", "cgroup"];
        checks.push(check(
            "namespace_interfaces",
            true,
            Some(namespaces.iter().all(|name| std::fs::metadata(format!("/proc/self/ns/{name}")).is_ok())),
            "Checks visibility of the required kernel namespace interfaces, not creation permission.",
            "Use a Linux environment exposing user, PID, network, mount, IPC, UTS and cgroup namespaces.",
        ));
        checks.push(check(
            "user_namespace_limit",
            true,
            Some(kernel_number("/proc/sys/user/max_user_namespaces").is_ok_and(|value| value > 0)),
            "Checks that the visible user-namespace limit is positive.",
            "Ask the execution owner to provide an environment configured for nested user namespaces.",
        ));
        let resource_result = isolation::resource_limits();
        checks.push(check(
            "cgroup_limits",
            true,
            Some(resource_result.is_ok()),
            "Uses isolate's private unified cgroup check and finite memory, PID and CPU ceilings.",
            "The owner must supply a private cgroup v2 namespace: memory<=4GiB, pids<=256, CPU<=8 cores.",
        ));
        if let Ok(value) = resource_result {
            limits = value;
        }
        checks.push(check(
            "namespace_creation_policy",
            false,
            None,
            "LSM, seccomp, nesting quotas and actual namespace creation are not exercised.",
            "Run a bounded isolate smoke test inside the intended execution environment.",
        ));
    } else if options.launcher.is_some() {
        checks.push(check(
            "isolation_launcher",
            false,
            None,
            "The run profile does not use the supplied isolation launcher.",
            "Select --profile isolate to check the namespace profile prerequisites.",
        ));
    }
    checks.push(check(
        "execution_and_external_owner",
        false,
        None,
        "Targets, workspace/stdio, report paths, resource ownership and teardown are not exercised.",
        "Follow docs/OPERATIONS.md; keep an external owner and validate each actual execution result.",
    ));
    let passed = checks
        .iter()
        .all(|row| !row.required || row.status == "pass");
    let report = json!({
        "schema": 1, "command": "doctor",
        "profile": if options.isolation { "isolate" } else { "run" },
        "authority": "diagnostic_only", "integrity_verified": false,
        "checks_passed": passed, "execution_tested": false,
        "measurement_contract": durante::report::measurement_contract(),
        "checks": checks, "cgroup_limits": limits,
    });
    let stdout = io::stdout();
    let mut output = stdout.lock();
    serde_json::to_writer_pretty(&mut output, &report)
        .map_err(io::Error::other)
        .and_then(|()| writeln!(output))
        .map_err(|_| "cannot write doctor result".to_owned())?;
    Ok(if passed { 0 } else { 1 })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::fd::AsRawFd;

    #[test]
    fn kernel_probe_preserves_open_descriptor_and_flags() {
        let file = tempfile::tempfile().unwrap();
        let fd = file.as_raw_fd();
        // Test an intentionally non-CLOEXEC descriptor too: changing that flag
        // would silently break a caller even if the descriptor remained open.
        assert_eq!(unsafe { libc::fcntl(fd, libc::F_SETFD, 0) }, 0);
        let _ = close_range_available();
        assert_eq!(unsafe { libc::fcntl(fd, libc::F_GETFD) }, 0);
        assert!(file.metadata().is_ok());
    }
}
