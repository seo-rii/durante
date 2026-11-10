//! A dedicated CLI supervisor, not an execution sandbox. It owns its direct and
//! reparented descendants, but it does not change target credentials or impose
//! CPU/RSS limits. The caller still supplies isolation and resource protection.
use serde_json::{json, Value};
use std::ffi::OsString;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{self, Read, Write};
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

const CLEANUP_TIMEOUT: Duration = Duration::from_secs(3);
const POLL_INTERVAL: Duration = Duration::from_millis(5);
const CHILD_LIST_BYTES: u64 = 65_536;
static CANCEL_SIGNAL: AtomicI32 = AtomicI32::new(0);

pub struct Options {
    pub drrun: PathBuf,
    pub client: PathBuf,
    pub report: PathBuf,
    pub timeout_ms: u64,
    pub records_dir: Option<PathBuf>,
    pub keep_records: bool,
    pub command: Vec<OsString>,
}

extern "C" fn cancelled(signal: libc::c_int) {
    // A lock-free atomic is the only operation performed in the signal handler.
    let _ = CANCEL_SIGNAL.compare_exchange(0, signal, Ordering::Relaxed, Ordering::Relaxed);
}

struct ProcessOwner {
    old_subreaper: libc::c_int,
    old_signals: Vec<(libc::c_int, libc::sigaction)>,
    old_mask: libc::sigset_t,
    mask_saved: bool,
}

impl ProcessOwner {
    fn new() -> io::Result<Self> {
        let mut old_subreaper = 0;
        // SAFETY: PR_GET_CHILD_SUBREAPER writes one c_int to a valid local.
        if unsafe { libc::prctl(libc::PR_GET_CHILD_SUBREAPER, &mut old_subreaper, 0, 0, 0) } < 0 {
            return Err(io::Error::last_os_error());
        }
        let mut owner = Self {
            old_subreaper,
            old_signals: Vec::new(),
            // SAFETY: initialized by sigprocmask before use.
            old_mask: unsafe { std::mem::zeroed() },
            mask_saved: false,
        };
        // SAFETY: PR_SET_CHILD_SUBREAPER takes the immediate integer flag.
        if unsafe { libc::prctl(libc::PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) } < 0 {
            return Err(io::Error::last_os_error());
        }
        CANCEL_SIGNAL.store(0, Ordering::Relaxed);
        for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGCHLD] {
            // SAFETY: zeroed sigaction and empty masks are initialized below.
            let mut action: libc::sigaction = unsafe { std::mem::zeroed() };
            let mut old: libc::sigaction = unsafe { std::mem::zeroed() };
            // SIGCHLD must not be SIG_IGN/SA_NOCLDWAIT: an owned child remains a
            // zombie until our waitpid, which prevents PID reuse during cleanup.
            action.sa_sigaction = if signal == libc::SIGCHLD {
                libc::SIG_DFL
            } else {
                cancelled as *const () as usize
            };
            // SAFETY: pointers refer to live sigaction/sigset_t locals.
            unsafe {
                libc::sigemptyset(&mut action.sa_mask);
                if libc::sigaction(signal, &action, &mut old) < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            owner.old_signals.push((signal, old));
        }
        // A CLI exec can inherit blocked signals. Make our cancellation
        // contract independent of the caller's SIGINT/SIGTERM mask.
        // SAFETY: valid local/saved sigsets, no pending default dispositions.
        unsafe {
            let mut mask: libc::sigset_t = std::mem::zeroed();
            libc::sigemptyset(&mut mask);
            libc::sigaddset(&mut mask, libc::SIGINT);
            libc::sigaddset(&mut mask, libc::SIGTERM);
            if libc::sigprocmask(libc::SIG_UNBLOCK, &mask, &mut owner.old_mask) < 0 {
                return Err(io::Error::last_os_error());
            }
        }
        owner.mask_saved = true;
        Ok(owner)
    }
}

impl Drop for ProcessOwner {
    fn drop(&mut self) {
        // Restoring is only reached after the supervised wait/cleanup attempt.
        for (signal, action) in self.old_signals.iter().rev() {
            // SAFETY: the saved dispositions came from sigaction itself.
            unsafe {
                libc::sigaction(*signal, action, std::ptr::null_mut());
            }
        }
        if self.mask_saved {
            // SAFETY: restore the exact mask returned by sigprocmask.
            unsafe {
                libc::sigprocmask(libc::SIG_SETMASK, &self.old_mask, std::ptr::null_mut());
            }
        }
        // SAFETY: saved immediate flag from PR_GET_CHILD_SUBREAPER.
        unsafe {
            libc::prctl(libc::PR_SET_CHILD_SUBREAPER, self.old_subreaper, 0, 0, 0);
        }
    }
}

struct Records {
    path: PathBuf,
    keep: bool,
}

impl Records {
    fn create(requested: Option<&Path>, keep: bool) -> io::Result<Self> {
        if let Some(path) = requested {
            DirBuilder::new().mode(0o700).create(path)?;
            return Ok(Self {
                path: fs::canonicalize(path)?,
                keep: true,
            });
        }
        let base = fs::canonicalize(std::env::temp_dir())?;
        let mut random = File::open("/dev/urandom")?;
        for _ in 0..16 {
            let mut nonce = [0_u8; 16];
            random.read_exact(&mut nonce)?;
            let name: String = nonce.iter().map(|byte| format!("{byte:02x}")).collect();
            let path = base.join(format!("durante-{}-{name}", std::process::id()));
            match DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => return Ok(Self { path, keep }),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "cannot create fresh record directory",
        ))
    }

    fn cleanup(&mut self) -> io::Result<()> {
        if !self.keep {
            fs::remove_dir_all(&self.path)?;
            self.keep = true; // Already removed; do not retry in Drop.
        }
        Ok(())
    }
}

impl Drop for Records {
    fn drop(&mut self) {
        if !self.keep {
            let _ = fs::remove_dir_all(&self.path);
        }
    }
}

#[derive(Default)]
struct WaitState {
    root_status: Option<i32>,
    reaped: usize,
}

// Returns true while at least one direct/reparented child is still live. The
// runner is a dedicated single-threaded process and has no unrelated children.
fn reap(root_pid: u32, state: &mut WaitState) -> io::Result<bool> {
    loop {
        let mut status = 0;
        // SAFETY: valid status pointer; no signal handler or other thread waits.
        let pid = unsafe { libc::waitpid(-1, &mut status, libc::WNOHANG) };
        if pid > 0 {
            state.reaped += 1;
            if pid as u32 == root_pid {
                state.root_status = Some(status);
            }
        } else if pid == 0 {
            return Ok(true);
        } else {
            let error = io::Error::last_os_error();
            match error.raw_os_error() {
                Some(libc::ECHILD) => return Ok(false),
                Some(libc::EINTR) => continue,
                _ => return Err(error),
            }
        }
    }
}

fn kill_owned_children() -> io::Result<()> {
    let pid = std::process::id();
    let file = File::open(format!("/proc/self/task/{pid}/children"))?;
    let mut bytes = Vec::new();
    file.take(CHILD_LIST_BYTES).read_to_end(&mut bytes)?;
    // /proc may exceed our read bound. Never turn the last partial token into
    // a different valid PID. A subsequent kill/reap pass handles the remainder.
    let complete_end = if bytes.len() < CHILD_LIST_BYTES as usize {
        bytes.len() // EOF proves that even a non-whitespace last token is whole.
    } else {
        bytes
            .iter()
            .rposition(|byte| byte.is_ascii_whitespace())
            .map_or(0, |position| position + 1)
    };
    for token in bytes[..complete_end]
        .split(|byte| byte.is_ascii_whitespace())
        .filter(|token| !token.is_empty())
    {
        let child = std::str::from_utf8(token)
            .ok()
            .and_then(|token| token.parse::<i32>().ok())
            .filter(|child| *child > 0)
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid owned child list")
            })?;
        // Every listed PID is our unreaped direct child. With SIGCHLD default
        // and this sole wait thread, it cannot be reused until a later reap.
        // Killing parents adopts even setsid/double-fork descendants; repeat.
        // SAFETY: positive PID obtained from the kernel's own-child list.
        if unsafe { libc::kill(child, libc::SIGKILL) } < 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(error);
            }
        }
    }
    Ok(())
}

fn cleanup_tree(root_pid: u32, state: &mut WaitState) -> bool {
    let started = Instant::now();
    loop {
        match reap(root_pid, state) {
            Ok(false) => return true,
            Err(_) => return false,
            Ok(true) => {}
        }
        if started.elapsed() >= CLEANUP_TIMEOUT {
            return false;
        }
        if kill_owned_children().is_err() {
            return false;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

fn invalid_measurement(root_pid: Option<u32>, reason: &str) -> Value {
    json!({"schema":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions",
        "scope":"process_tree_user_code","valid":false,"complete":false,
        "root_pid":root_pid,"reason":reason})
}

fn trusted_installation(path: &Path, executable: bool) -> io::Result<PathBuf> {
    let path = fs::canonicalize(path)?;
    let metadata = fs::metadata(&path)?;
    let mode = metadata.permissions().mode();
    if !metadata.is_file() || mode & 0o022 != 0 || (executable && mode & 0o111 == 0) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "invalid trusted installation",
        ));
    }
    Ok(path)
}

pub fn run(options: Options) -> Result<i32, String> {
    if options.command.is_empty() || !(1..=86_400_000).contains(&options.timeout_ms) {
        return Err("target command and bounded positive timeout are required".to_owned());
    }
    let drrun = trusted_installation(&options.drrun, true)
        .map_err(|_| "launcher must be a trusted regular executable".to_owned())?;
    let client = trusted_installation(&options.client, false)
        .map_err(|_| "client must be a trusted regular file".to_owned())?;
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&options.report)
        .map_err(|_| {
            "cannot create a new report file (existing paths are never replaced)".to_owned()
        })?;
    let started = Instant::now();
    let mut result = json!({"schema":1,"authority":"diagnostic_only",
        "execution":{"state":"launch_failed","root_pid":null,"exit_code":null,
            "signal":null,"cancel_signal":null,"wall_time_ms":0,"cleanup_time_ms":0,
            "cleanup_complete":true,"records_cleanup_complete":true,
            "supervisor_reaped_processes":0},
        "measurement":invalid_measurement(None,"measurement did not start")});
    let outcome = execute(&options, drrun, client, &mut result);
    result["execution"]["wall_time_ms"] =
        json!(started.elapsed().as_millis().min(u64::MAX as u128) as u64);
    let exit = match outcome {
        Ok(code) => code,
        Err(reason) => {
            result["measurement"] = invalid_measurement(None, reason);
            1
        }
    };
    serde_json::to_writer_pretty(&mut output, &result)
        .map_err(|_| "cannot write report".to_owned())?;
    writeln!(output)
        .and_then(|()| output.sync_data())
        .map_err(|_| "cannot finish report".to_owned())?;
    Ok(exit)
}

fn execute(
    options: &Options,
    drrun: PathBuf,
    client: PathBuf,
    result: &mut Value,
) -> Result<i32, &'static str> {
    let mut records = Records::create(options.records_dir.as_deref(), options.keep_records)
        .map_err(|_| "cannot create a fresh private records directory")?;
    if records.keep {
        result["execution"]["records_dir"] = json!(records.path);
    }
    let _owner = ProcessOwner::new().map_err(|_| "cannot establish process-tree ownership")?;
    let parent_pid = std::process::id() as libc::pid_t;
    let mut command = Command::new(drrun);
    command
        .args([
            "-quiet",
            "-follow_children",
            "-vm_size",
            "32M",
            "-vmheap_size",
            "32M",
            "-c",
        ])
        .arg(client)
        .arg("-outdir")
        .arg(&records.path)
        .args(["-rep_bytes", "--"])
        .args(&options.command)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .process_group(0);
    // Only async-signal-safe libc calls occur between fork and exec. No allocation,
    // environment access, formatting, locks, or Rust signal machinery occurs here.
    unsafe {
        command.pre_exec(move || {
            let mut action: libc::sigaction = std::mem::zeroed();
            action.sa_sigaction = libc::SIG_DFL;
            libc::sigemptyset(&mut action.sa_mask);
            for signal in [libc::SIGINT, libc::SIGTERM, libc::SIGPIPE] {
                if libc::sigaction(signal, &action, std::ptr::null_mut()) < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) < 0 {
                return Err(io::Error::last_os_error());
            }
            if libc::getppid() != parent_pid {
                return Err(io::Error::from_raw_os_error(libc::ECANCELED));
            }
            Ok(())
        });
    }
    let already_cancelled = CANCEL_SIGNAL.load(Ordering::Relaxed);
    if already_cancelled != 0 {
        result["execution"]["state"] = json!("cancelled");
        result["execution"]["cancel_signal"] = json!(already_cancelled);
        result["measurement"] = invalid_measurement(None, "measurement cancelled before launch");
        records
            .cleanup()
            .map_err(|_| "cannot remove records directory")?;
        return Ok(128 + already_cancelled);
    }
    let child = command
        .spawn()
        .map_err(|_| "measurement engine launch failed")?;
    let root_pid = child.id();
    // This dedicated supervisor uses waitpid for every child, including the
    // root. Dropping Child does not kill or wait; no second reaper is created.
    drop(child);
    result["execution"]["root_pid"] = json!(root_pid);
    let deadline = Instant::now() + Duration::from_millis(options.timeout_ms);
    let mut waited = WaitState::default();
    let (state, mut code, forced) = loop {
        let signal = CANCEL_SIGNAL.load(Ordering::Relaxed);
        if signal != 0 {
            result["execution"]["cancel_signal"] = json!(signal);
            break ("cancelled", 128 + signal, true);
        }
        match reap(root_pid, &mut waited) {
            Ok(false) => break ("complete", 0, false),
            Err(_) => break ("wait_failed", 1, true),
            Ok(true) => {}
        }
        if Instant::now() >= deadline {
            break ("timed_out", 124, true);
        }
        std::thread::sleep(POLL_INTERVAL);
    };
    result["execution"]["state"] = json!(state);
    let cleanup_start = Instant::now();
    let cleanup_complete = !forced || cleanup_tree(root_pid, &mut waited);
    result["execution"]["cleanup_time_ms"] = json!(cleanup_start.elapsed().as_millis() as u64);
    result["execution"]["cleanup_complete"] = json!(cleanup_complete);
    result["execution"]["supervisor_reaped_processes"] = json!(waited.reaped);
    let normal_exit = if let Some(status) = waited.root_status {
        if libc::WIFEXITED(status) {
            result["execution"]["exit_code"] = json!(libc::WEXITSTATUS(status));
            true
        } else {
            if libc::WIFSIGNALED(status) {
                result["execution"]["signal"] = json!(libc::WTERMSIG(status));
            }
            false
        }
    } else {
        false
    };
    if !cleanup_complete {
        result["execution"]["state"] = json!("cleanup_failed");
        result["measurement"] = invalid_measurement(
            Some(root_pid),
            "process-tree cleanup deadline or operation failed",
        );
        code = 1;
    } else if forced {
        // Do not trust a syntactically complete application-writable report
        // after cancellation or a forced resource stop.
        result["measurement"] = invalid_measurement(Some(root_pid), "measurement forcibly stopped");
    } else if !normal_exit {
        result["execution"]["state"] = json!("incomplete");
        result["measurement"] = invalid_measurement(Some(root_pid), "root did not exit normally");
        code = 1;
    } else {
        let mut measurement = durante::collect::collect(&records.path, root_pid);
        if measurement["valid"] == true && measurement["complete"] == true {
            let recorded_exit = measurement["processes"]
                .as_array()
                .and_then(|processes| processes.iter().find(|process| process["pid"] == root_pid))
                .and_then(|process| process["exit_code"].as_i64());
            if recorded_exit != result["execution"]["exit_code"].as_i64() {
                measurement =
                    invalid_measurement(Some(root_pid), "root exit status disagrees with records");
            }
        }
        if measurement["valid"] != true || measurement["complete"] != true {
            result["execution"]["state"] = json!("incomplete");
            code = 1;
        }
        result["measurement"] = measurement;
    }
    // An unreaped process could still access its records. Preserve them instead
    // of claiming successful cleanup or collecting a live tree.
    if !cleanup_complete {
        records.keep = true;
        result["execution"]["records_dir"] = json!(records.path);
    }
    if records.cleanup().is_err() {
        result["execution"]["state"] = json!("cleanup_failed");
        result["execution"]["records_cleanup_complete"] = json!(false);
        result["execution"]["records_dir"] = json!(records.path);
        result["measurement"] =
            invalid_measurement(Some(root_pid), "record-directory cleanup failed");
        code = 1;
    }
    Ok(code)
}
