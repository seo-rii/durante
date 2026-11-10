#![cfg(all(target_os = "linux", target_arch = "x86_64"))]
use std::fs;
use std::os::fd::AsRawFd;
use std::os::unix::fs::PermissionsExt;
use std::os::unix::process::CommandExt;
use std::path::PathBuf;
use std::process::{Command, Output};
use std::time::{Duration, Instant};

const LAUNCHER: &str = r#"#!/bin/sh
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
{"schema":1,"client_version":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions","pid":$$,"segment":"$$-1","seq":2,"event":"end","instructions":10,"rep_bytes":0,"rep_entries":0,"unsupported_rep_entries":0,"signals_observed":0,"threads_started":1,"threads_finished":1,"threads_complete":true,"accounting_error":false,"valid":true,"quality":"uninterrupted","app_exit_seen":true,"app_exit_code":0}
JSON
if [ "${SECURITY_NESTED:-0}" = 1 ]; then
 mkdir "$out/retained"
 printf witness > "$out/retained/marker"
 printf '%s' "$out" > "$SECURITY_RECORDS_PATH"
fi
exec "$@"
"#;

// This hostile target only acts on the dedicated supervisor that launched it
// and paths in this test's private directory. Its finite churn has at most eight
// live children and 500 creations, and every child exits after ten milliseconds.
const TARGET: &str = r#"
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/prctl.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
int main(int argc, char **argv) {
    if (argc < 2) return 2;
    if (!strcmp(argv[1], "proc")) {
        char path[128];
        snprintf(path, sizeof(path), "/proc/%ld/mem", (long)getppid());
        int fd = open(path, O_RDONLY | O_CLOEXEC);
        if (fd >= 0) { close(fd); return 41; }
        if (errno != EACCES && errno != EPERM) return 42;
        snprintf(path, sizeof(path), "/proc/%ld/fd", (long)getppid());
        DIR *directory = opendir(path);
        if (directory) { closedir(directory); return 43; }
        if (errno != EACCES && errno != EPERM) return 44;
        puts("parent protected"); return 0;
    }
    if (!strcmp(argv[1], "nnp")) {
        if (prctl(PR_GET_NO_NEW_PRIVS, 0, 0, 0, 0) != 1) return 9;
        puts("no new privileges"); return 0;
    }
    if (!strcmp(argv[1], "nested")) return 0;
    if (!strcmp(argv[1], "extend") && argc == 3) {
        int fd = open(argv[2], O_WRONLY | O_CLOEXEC);
        if (fd < 0) return 10;
        int result = ftruncate(fd, 4096);
        close(fd); return result ? 11 : 0;
    }
    if (!strcmp(argv[1], "fd")) {
        errno = 0;
        if (fcntl(200, F_GETFD) != -1 || errno != EBADF) return 12;
        puts("secret fd closed"); return 0;
    }
    if (!strcmp(argv[1], "replace") && argc == 4) {
        if (unlink(argv[2]) || symlink(argv[3], argv[2])) return 3;
        return 0;
    }
    if (!strcmp(argv[1], "hardlink") && argc == 4)
        return link(argv[2], argv[3]) ? 4 : 0;
    if (!strcmp(argv[1], "churn")) {
        for (int made = 0; made < 500;) {
            int live = 0;
            for (; live < 8 && made < 500; ++live, ++made) {
                pid_t pid = fork();
                if (pid < 0) return 5;
                if (pid == 0) {
                    if (prctl(PR_SET_PDEATHSIG, SIGKILL, 0, 0, 0)) _exit(6);
                    usleep(10000); _exit(0);
                }
            }
            while (live) {
                if (waitpid(-1, NULL, 0) > 0) --live;
                else if (errno != EINTR) return 7;
            }
        }
        return 0;
    }
    return 8;
}
"#;

struct Fixture {
    _dir: tempfile::TempDir,
    launcher: PathBuf,
    client: PathBuf,
    target: PathBuf,
    report: PathBuf,
}
impl Fixture {
    fn new() -> Self {
        let dir = tempfile::tempdir().unwrap();
        let launcher = dir.path().join("launcher");
        let client = dir.path().join("client.so");
        let target = dir.path().join("target");
        let source = dir.path().join("target.c");
        let report = dir.path().join("report.json");
        fs::write(&launcher, LAUNCHER).unwrap();
        fs::set_permissions(&launcher, fs::Permissions::from_mode(0o700)).unwrap();
        fs::write(&client, b"trusted fixture").unwrap();
        fs::set_permissions(&client, fs::Permissions::from_mode(0o600)).unwrap();
        fs::write(&source, TARGET).unwrap();
        let compiled = Command::new("gcc")
            .args(["-O2", "-Wall", "-Wextra", "-Werror"])
            .arg(&source)
            .arg("-o")
            .arg(&target)
            .output()
            .unwrap();
        assert!(compiled.status.success(), "{compiled:?}");
        Self {
            _dir: dir,
            launcher,
            client,
            target,
            report,
        }
    }
    fn command(&self, mode: &str, extra: &[&std::path::Path], timeout: u64) -> Command {
        let mut command = Command::new(env!("CARGO_BIN_EXE_durante"));
        command
            .env("SECURITY_NESTED", if mode == "nested" { "1" } else { "0" })
            .env(
                "SECURITY_RECORDS_PATH",
                self._dir.path().join("records-path"),
            )
            .arg("run")
            .arg("--drrun")
            .arg(&self.launcher)
            .arg("--client")
            .arg(&self.client)
            .arg("--report")
            .arg(&self.report)
            .arg("--timeout-ms")
            .arg(timeout.to_string())
            .arg("--")
            .arg(&self.target)
            .arg(mode)
            .args(extra);
        command
    }
    fn run(&self, mode: &str, extra: &[&std::path::Path], timeout: u64) -> Output {
        self.command(mode, extra, timeout).output().unwrap()
    }
}

#[test]
fn proc_parent_descriptors_and_memory_are_inaccessible() {
    let fixture = Fixture::new();
    let output = fixture.run("proc", &[], 2000);
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stdout, b"parent protected\n");
}

#[test]
fn report_replacement_symlink_is_rejected() {
    let fixture = Fixture::new();
    let victim = fixture._dir.path().join("victim");
    fs::write(&victim, b"do not modify").unwrap();
    let output = fixture.run("replace", &[&fixture.report, &victim], 2000);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    assert_eq!(fs::read(victim).unwrap(), b"do not modify");
}

#[test]
fn report_hardlink_alias_is_rejected() {
    let fixture = Fixture::new();
    let alias = fixture._dir.path().join("alias");
    let output = fixture.run("hardlink", &[&fixture.report, &alias], 2000);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
}

#[test]
fn finite_child_churn_does_not_defeat_deadline() {
    let fixture = Fixture::new();
    let started = Instant::now();
    let output = fixture.run("churn", &[], 30);
    assert_eq!(output.status.code(), Some(124), "{output:?}");
    assert!(started.elapsed() < Duration::from_secs(3));
    let result: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.report).unwrap()).unwrap();
    assert_eq!(result["execution"]["cleanup_complete"], true);
    assert_eq!(result["measurement"]["valid"], false);
    assert!(result["measurement"].get("instructions").is_none());
}

#[test]
fn child_no_new_privileges_is_set() {
    let fixture = Fixture::new();
    let output = fixture.run("nnp", &[], 2000);
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stdout, b"no new privileges\n");
}

#[test]
fn untrusted_nested_record_directory_is_preserved_with_cleanup_failure() {
    let fixture = Fixture::new();
    let output = fixture.run("nested", &[], 2000);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
    let result: serde_json::Value =
        serde_json::from_slice(&fs::read(&fixture.report).unwrap()).unwrap();
    let records =
        PathBuf::from(fs::read_to_string(fixture._dir.path().join("records-path")).unwrap());
    let witness = records.join("retained/marker");
    let state = result["execution"]["state"].clone();
    let preserved = fs::read(&witness).ok();
    // Remove only this fixture's own retained directory after observing it.
    let _ = fs::remove_dir_all(&records);
    assert_eq!(state, "cleanup_failed");
    assert_eq!(result["execution"]["records_cleanup_complete"], false);
    assert_eq!(preserved.as_deref(), Some(b"witness".as_slice()));
    assert_eq!(result["measurement"]["valid"], false);
    assert!(result["measurement"].get("instructions").is_none());
}

#[test]
fn inplace_report_extension_is_rejected() {
    let fixture = Fixture::new();
    let output = fixture.run("extend", &[&fixture.report], 2000);
    assert_eq!(output.status.code(), Some(1), "{output:?}");
}

#[test]
fn non_cloexec_fixture_secret_is_not_inherited_by_target() {
    let fixture = Fixture::new();
    let secret_path = fixture._dir.path().join("secret");
    fs::write(&secret_path, b"synthetic fixture secret").unwrap();
    let secret = fs::File::open(&secret_path).unwrap();
    let source = secret.as_raw_fd();
    let mut command = fixture.command("fd", &[], 2000);
    // Only the dedicated CLI child gets FD 200. The fixture does not change the
    // test process's descriptor table or expose any real host credential.
    unsafe {
        command.pre_exec(move || {
            if libc::dup2(source, 200) < 0 || libc::fcntl(200, libc::F_SETFD, 0) < 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let output = command.output().unwrap();
    assert!(output.status.success(), "{output:?}");
    assert_eq!(output.stdout, b"secret fd closed\n");
}
