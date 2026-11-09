#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
use serde_json::Value;
use std::fs;
use std::os::unix::fs::{symlink, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant};

const MOCK: &str = r#"#!/bin/sh
set -eu
out=''
while [ "$#" -gt 0 ]; do
 case "$1" in
 -outdir) out="$2"; shift 2 ;;
 --) shift; break ;;
 *) shift ;;
 esac
done
cat > "$out/process-$$.jsonl" <<JSON
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":1,"event":"start","origin":"initial","parent_pid":0,"parent_segment":"","rep_bytes_enabled":true,"sdk_version":"11.3.0"}
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"end","instructions":10,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,"valid":true,"quality":"uninterrupted","app_exit_seen":true,"app_exit_code":${MOCK_EXIT_CODE:-0}}
JSON
exec "$@"
"#;

struct Fixture {
    dir: tempfile::TempDir,
    launcher: PathBuf,
    client: PathBuf,
    report: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let launcher = dir.path().join("drrun");
        let client = dir.path().join("client.so");
        let report = dir.path().join("result.json");
        fs::write(&launcher, MOCK).unwrap();
        fs::set_permissions(&launcher, fs::Permissions::from_mode(0o755)).unwrap();
        fs::write(&client, b"trusted mock client").unwrap();
        fs::set_permissions(&client, fs::Permissions::from_mode(0o600)).unwrap();
        Self {
            dir,
            launcher,
            client,
            report,
        }
    }
    fn command(&self, timeout: u64) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_workmeter"));
        command
            .args(["run", "--drrun"])
            .arg(&self.launcher)
            .arg("--client")
            .arg(&self.client)
            .arg("--report")
            .arg(&self.report)
            .args(["--timeout-ms", &timeout.to_string()]);
        command
    }
    fn run(&self, args: &[&str]) -> Output {
        self.command(2000).arg("--").args(args).output().unwrap()
    }
    fn result(&self) -> Value {
        serde_json::from_slice(&fs::read(&self.report).unwrap()).unwrap()
    }
}
fn absent(pid: u32) -> bool {
    // Checking /proc also rejects leaving an unreaped zombie behind.
    !Path::new(&format!("/proc/{pid}")).exists()
}
fn await_file(path: &Path, child: &mut std::process::Child) {
    let until = Instant::now() + Duration::from_secs(3);
    while !path.exists() {
        assert!(
            child.try_wait().unwrap().is_none(),
            "supervisor stopped before fixture became ready"
        );
        if Instant::now() >= until {
            let _ = child.kill();
            let _ = child.wait();
            panic!("fixture did not become ready");
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}

#[test]
fn target_stdio_and_diagnostic_exit_are_separate() {
    let f = Fixture::new();
    let output = f
        .command(2000)
        .env("MOCK_EXIT_CODE", "7")
        .args([
            "--",
            "/bin/sh",
            "-c",
            "printf target-out; printf target-err >&2; exit 7",
        ])
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stdout, b"target-out");
    assert_eq!(output.stderr, b"target-err");
    let result = f.result();
    assert_eq!(result["execution"]["state"], "complete");
    assert_eq!(result["execution"]["exit_code"], 7);
    assert_eq!(result["measurement"]["valid"], true);
    assert_eq!(result["measurement"]["instructions"], 10);
    assert_eq!(result["execution"]["cleanup_complete"], true);
    assert_eq!(
        fs::metadata(&f.report).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert!(absent(
        result["execution"]["root_pid"].as_u64().unwrap() as u32
    ));
}

#[test]
fn report_collision_and_symlink_cannot_launch_or_clobber() {
    for link in [false, true] {
        let f = Fixture::new();
        let original = f.dir.path().join("original");
        fs::write(&original, "unchanged").unwrap();
        if link {
            symlink(&original, &f.report).unwrap();
        } else {
            fs::write(&f.report, "unchanged").unwrap();
        }
        let marker = f.dir.path().join("must-not-run");
        let output = f
            .command(2000)
            .args(["--", "/bin/sh", "-c", "touch \"$1\"", "sh"])
            .arg(&marker)
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(1));
        assert!(!marker.exists());
        assert_eq!(fs::read_to_string(&f.report).unwrap(), "unchanged");
        assert_eq!(fs::read_to_string(&original).unwrap(), "unchanged");
    }
}

#[test]
fn records_are_private_explicit_and_collectable_without_python() {
    let f = Fixture::new();
    let records = f.dir.path().join("records");
    let output = f
        .command(2000)
        .arg("--records-dir")
        .arg(&records)
        .args(["--", "/bin/true"])
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(
        fs::metadata(&records).unwrap().permissions().mode() & 0o777,
        0o700
    );
    let report = f.result();
    let collect = Command::new(env!("CARGO_BIN_EXE_workmeter"))
        .args(["collect", "--dir"])
        .arg(&records)
        .arg("--root-pid")
        .arg(report["execution"]["root_pid"].to_string())
        .output()
        .unwrap();
    assert!(collect.status.success(), "{collect:?}");
    assert_eq!(
        serde_json::from_slice::<Value>(&collect.stdout).unwrap(),
        report["measurement"]
    );
    let f2 = Fixture::new();
    let output = f2
        .command(2000)
        .arg("--records-dir")
        .arg(&records)
        .args(["--", "/bin/true"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(f2.result()["measurement"]["valid"], false);
}

#[test]
fn automatically_created_records_are_cleaned_or_explicitly_retained() {
    for keep in [false, true] {
        let f = Fixture::new();
        let temp = f.dir.path().join("temp");
        fs::create_dir(&temp).unwrap();
        let mut command = f.command(2000);
        command.env("TMPDIR", &temp);
        if keep {
            command.arg("--keep-records");
        }
        let output = command.args(["--", "/bin/true"]).output().unwrap();
        assert!(output.status.success(), "{output:?}");
        let count = fs::read_dir(&temp).unwrap().count();
        assert_eq!(count, usize::from(keep));
        assert_eq!(f.result()["execution"]["records_dir"].is_string(), keep);
    }
}

#[test]
fn timeout_never_accepts_a_preexisting_complete_counter() {
    let f = Fixture::new();
    let started = Instant::now();
    let output = f
        .command(80)
        .args(["--", "/bin/sleep", "30"])
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(124), "{output:?}");
    assert!(started.elapsed() < Duration::from_secs(4));
    let result = f.result();
    assert_eq!(result["execution"]["state"], "timed_out");
    assert_eq!(result["execution"]["cleanup_complete"], true);
    assert_eq!(result["measurement"]["valid"], false);
    assert!(result["measurement"].get("instructions").is_none());
    assert!(absent(
        result["execution"]["root_pid"].as_u64().unwrap() as u32
    ));
}

#[test]
fn parent_exit_waits_for_normal_descendants() {
    let f = Fixture::new();
    let marker = f.dir.path().join("completed");
    let started = Instant::now();
    let output = f
        .command(2000)
        .args([
            "--",
            "/bin/sh",
            "-c",
            "(sleep 0.15; printf finished > \"$1\") &",
            "sh",
        ])
        .arg(&marker)
        .output()
        .unwrap();
    assert!(output.status.success(), "{output:?}");
    assert!(started.elapsed() >= Duration::from_millis(100));
    assert_eq!(fs::read_to_string(&marker).unwrap(), "finished");
    assert_eq!(f.result()["execution"]["cleanup_complete"], true);
    assert!(
        f.result()["execution"]["supervisor_reaped_processes"]
            .as_u64()
            .unwrap()
            >= 2
    );
}

#[test]
fn cancellation_reaps_a_descendant_that_escaped_the_process_group() {
    let f = Fixture::new();
    let pidfile = f.dir.path().join("escaped.pid");
    let mut child = f
        .command(10_000)
        .args([
            "--",
            "/bin/sh",
            "-c",
            "setsid /bin/sh -c 'printf %s $$ > \"$1\"; exec sleep 30' sh \"$1\" & wait",
            "sh",
        ])
        .arg(&pidfile)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    await_file(&pidfile, &mut child);
    let escaped: u32 = fs::read_to_string(&pidfile).unwrap().parse().unwrap();
    // SAFETY: PID is our running child process.
    assert_eq!(unsafe { libc::kill(child.id() as i32, libc::SIGTERM) }, 0);
    let status = child.wait().unwrap();
    assert_eq!(status.code(), Some(128 + libc::SIGTERM));
    let result = f.result();
    assert_eq!(result["execution"]["state"], "cancelled");
    assert_eq!(result["execution"]["cleanup_complete"], true);
    assert_eq!(result["measurement"]["valid"], false);
    assert!(absent(escaped), "escaped descendant was not reaped");
    assert!(absent(
        result["execution"]["root_pid"].as_u64().unwrap() as u32
    ));
}

#[test]
fn inherited_ignored_sigchld_does_not_lose_wait_status() {
    use std::os::unix::process::CommandExt;
    let f = Fixture::new();
    let mut command = f.command(2000);
    // SAFETY: only a sigaction syscall occurs in the child before exec.
    unsafe {
        command.pre_exec(|| {
            libc::signal(libc::SIGCHLD, libc::SIG_IGN);
            Ok(())
        });
    }
    let output = command.args(["--", "/bin/true"]).output().unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(f.result()["execution"]["exit_code"], 0);
}

#[test]
fn signalled_root_cannot_claim_complete_measurement() {
    let f = Fixture::new();
    let output = f.run(&["/bin/sh", "-c", "kill -KILL $$"]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(f.result()["execution"]["signal"], libc::SIGKILL);
    assert_eq!(f.result()["measurement"]["valid"], false);
}

#[test]
fn missing_records_are_unavailable_and_arguments_are_not_reported() {
    let f = Fixture::new();
    fs::write(&f.launcher, "#!/bin/sh\nexit 0\n").unwrap();
    let output = f.run(&["/bin/true", "private-value-sentinel"]);
    assert_eq!(output.status.code(), Some(1));
    assert_eq!(f.result()["execution"]["state"], "incomplete");
    assert!(!fs::read_to_string(&f.report)
        .unwrap()
        .contains("private-value-sentinel"));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("private-value-sentinel"));
}

#[test]
fn malformed_timeout_is_rejected_before_execution() {
    let f = Fixture::new();
    let output = f.command(0).args(["--", "/bin/true"]).output().unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(!f.report.exists());
}

#[test]
fn cancellation_unblocks_an_inherited_signal_mask() {
    use std::os::unix::process::CommandExt;
    let f = Fixture::new();
    let marker = f.dir.path().join("ready");
    let mut command = f.command(1000);
    // SAFETY: only signal-mask syscalls occur between fork and exec.
    unsafe {
        command.pre_exec(|| {
            let mut blocked: libc::sigset_t = std::mem::zeroed();
            libc::sigemptyset(&mut blocked);
            libc::sigaddset(&mut blocked, libc::SIGTERM);
            if libc::sigprocmask(libc::SIG_BLOCK, &blocked, std::ptr::null_mut()) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = command
        .args(["--", "/bin/sh", "-c", "touch \"$1\"; exec sleep 30", "sh"])
        .arg(&marker)
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    await_file(&marker, &mut child);
    // SAFETY: our supervised child PID is live and unreaped.
    assert_eq!(unsafe { libc::kill(child.id() as i32, libc::SIGTERM) }, 0);
    assert_eq!(child.wait().unwrap().code(), Some(128 + libc::SIGTERM));
    assert_eq!(f.result()["execution"]["state"], "cancelled");
}

#[test]
fn root_exit_disagreement_is_not_a_complete_measurement() {
    let f = Fixture::new();
    let output = f.run(&["/bin/sh", "-c", "exit 7"]);
    assert_eq!(output.status.code(), Some(1));
    let result = f.result();
    assert_eq!(result["execution"]["exit_code"], 7);
    assert_eq!(result["measurement"]["valid"], false);
    assert!(result["measurement"].get("instructions").is_none());
}

#[test]
fn parser_errors_do_not_echo_private_argument_values() {
    let f = Fixture::new();
    let output = f
        .command(2000)
        .arg("private-argument-sentinel")
        .output()
        .unwrap();
    assert_eq!(output.status.code(), Some(2));
    assert!(!String::from_utf8_lossy(&output.stderr).contains("private-argument-sentinel"));
    assert!(!f.report.exists());
}
