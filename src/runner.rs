//! A dedicated CLI supervisor, not an execution sandbox. It owns its direct and
//! reparented descendants, but it does not change target credentials or impose
//! CPU/RSS limits. The caller still supplies isolation and resource protection.
use crate::work_budget::{self, Clock};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::ffi::OsString;
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{self, Read, Seek, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicI32, Ordering};
use std::time::{Duration, Instant};

const CLEANUP_TIMEOUT: Duration = Duration::from_secs(3);
pub(crate) const POLL_INTERVAL: Duration = Duration::from_millis(5);
const CHILD_LIST_BYTES: u64 = 65_536;
const REAP_BATCH: usize = 64;
static CANCEL_SIGNAL: AtomicI32 = AtomicI32::new(0);

pub struct Options {
    pub drrun: PathBuf,
    pub client: PathBuf,
    pub report: PathBuf,
    pub report_existing: bool,
    pub timeout_ms: u64,
    pub records_dir: Option<PathBuf>,
    pub keep_records: bool,
    pub disallowed_cpu_features: Vec<String>,
    /// Fixed virtual processor identity; `None` exposes host CPUID.
    pub cpuid_profile: Option<String>,
    /// Also charge the static per-block latency bound (version 1).
    pub latency_bound: bool,
    /// Request the versioned static memory operand byte counter alongside the latency bound.
    pub memory_operand_bytes: bool,
    pub work_budget: Option<u64>,
    /// Isolate the meter from the application (client `-protect_meter`).
    pub protect_meter: bool,
    pub command: Vec<OsString>,
}

/// The prepared runtime's private window for every runtime allocation when the
/// meter is protected. It is reserved before the application image is mapped.
pub(crate) const METER_WINDOW: &str = "0x600000000000";

extern "C" fn cancelled(signal: libc::c_int) {
    // A lock-free atomic is the only operation performed in the signal handler.
    let _ = CANCEL_SIGNAL.compare_exchange(0, signal, Ordering::Relaxed, Ordering::Relaxed);
}

pub(crate) fn protect_supervisor() -> io::Result<()> {
    // The dedicated CLI stays non-dumpable until exit, including if cleanup
    // leaves a live target. This does not isolate signals or the filesystem.
    // SAFETY: prctl uses immediate integer arguments only.
    if unsafe { libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

pub(crate) struct ProcessOwner {
    old_subreaper: libc::c_int,
    old_signals: Vec<(libc::c_int, libc::sigaction)>,
    old_mask: libc::sigset_t,
    mask_saved: bool,
}

impl ProcessOwner {
    pub(crate) fn new() -> io::Result<Self> {
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

pub(crate) struct Records {
    pub(crate) path: PathBuf,
    keep: bool,
    directory: File,
}

impl Records {
    pub(crate) fn create(requested: Option<&Path>, keep: bool) -> io::Result<Self> {
        if let Some(path) = requested {
            DirBuilder::new().mode(0o700).create(path)?;
            return Self::opened(fs::canonicalize(path)?, true);
        }
        let base = fs::canonicalize(std::env::temp_dir())?;
        let mut random = File::open("/dev/urandom")?;
        for _ in 0..16 {
            let mut nonce = [0_u8; 16];
            random.read_exact(&mut nonce)?;
            let name: String = nonce.iter().map(|byte| format!("{byte:02x}")).collect();
            let path = base.join(format!("durante-{}-{name}", std::process::id()));
            match DirBuilder::new().mode(0o700).create(&path) {
                Ok(()) => return Self::opened(path, keep),
                Err(error) if error.kind() == io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(error),
            }
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "cannot create fresh record directory",
        ))
    }

    fn opened(path: PathBuf, keep: bool) -> io::Result<Self> {
        let directory = OpenOptions::new()
            .read(true)
            .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&path)?;
        Ok(Self {
            path,
            keep,
            directory,
        })
    }

    fn cleanup(&mut self) -> io::Result<()> {
        if !self.keep {
            // Never recursively walk application-created directories or retry
            // failed cleanup from Drop. Preserve unexpected contents for an
            // external resource owner instead of doing unbounded attacker work.
            self.keep = true;
            let original = self.directory.metadata()?;
            let same_directory = || -> io::Result<()> {
                let current = fs::symlink_metadata(&self.path)?;
                if !current.is_dir()
                    || current.dev() != original.dev()
                    || current.ino() != original.ino()
                {
                    return Err(io::Error::other("record directory was replaced"));
                }
                Ok(())
            };
            same_directory()?;
            let alias = format!("/proc/self/fd/{}", self.directory.as_raw_fd());
            let mut names = Vec::new();
            for entry in fs::read_dir(alias)? {
                let entry = entry?;
                if names.len() >= durante::collect::MAX_PROCESSES || !entry.file_type()?.is_file() {
                    return Err(io::Error::other("unexpected record cleanup contents"));
                }
                names.push(std::ffi::CString::new(entry.file_name().as_bytes())?);
            }
            for name in names {
                // SAFETY: each name is one directory leaf, and the held FD pins
                // the created directory even if its pathname is replaced.
                if unsafe { libc::unlinkat(self.directory.as_raw_fd(), name.as_ptr(), 0) } < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            same_directory()?;
            fs::remove_dir(&self.path)?;
        }
        Ok(())
    }
}

impl Drop for Records {
    fn drop(&mut self) {
        if !self.keep {
            let _ = self.cleanup();
        }
    }
}

#[derive(Default)]
pub(crate) struct WaitState {
    pub(crate) root_status: Option<i32>,
    pub(crate) reaped: usize,
    pending_owner_kills: BTreeSet<i32>,
    killed_during_cleanup: usize,
    other_child_signal: Option<i32>,
}

impl WaitState {
    fn record_exit(&mut self, root_pid: u32, pid: i32, status: i32) {
        self.reaped += 1;
        if pid as u32 == root_pid {
            self.root_status = Some(status);
        }
        let sent_kill = self.pending_owner_kills.remove(&pid);
        if libc::WIFSIGNALED(status) {
            let signal = libc::WTERMSIG(status);
            if signal == libc::SIGKILL && sent_kill {
                self.killed_during_cleanup += 1;
            } else {
                self.other_child_signal.get_or_insert(signal);
            }
        }
    }
}

/// The pause between supervisor passes. With a pidfd for the root it ends as
/// soon as the root exits, so a finished run is not left waiting for the next
/// interval. Every other condition is still checked once per interval.
pub(crate) struct RootWatch {
    root: Option<OwnedFd>,
}

impl RootWatch {
    pub(crate) fn new(root_pid: u32) -> Self {
        // The root is our own unreaped child, so its PID cannot have been
        // reused. Kernels before 5.3 and sandboxes that deny the call fall
        // back to interval polling.
        // SAFETY: integer arguments only.
        let fd = unsafe { libc::syscall(libc::SYS_pidfd_open, root_pid as libc::pid_t, 0) };
        // SAFETY: a non-negative result is a new close-on-exec descriptor
        // that nothing else owns.
        let root = (fd >= 0).then(|| unsafe { OwnedFd::from_raw_fd(fd as RawFd) });
        Self { root }
    }

    pub(crate) fn pause(&mut self, state: &WaitState, interval: Duration) {
        if state.root_status.is_some() {
            // A reaped root leaves its descriptor readable forever.
            self.root = None;
        }
        let Some(root) = &self.root else {
            std::thread::sleep(interval);
            return;
        };
        let mut poll = libc::pollfd {
            fd: root.as_raw_fd(),
            events: libc::POLLIN,
            revents: 0,
        };
        let timeout = interval.as_millis().min(libc::c_int::MAX as u128) as libc::c_int;
        // SAFETY: one valid pollfd. A signal ends the pause early; the caller
        // checks cancellation on every pass.
        let ready = unsafe { libc::poll(&mut poll, 1, timeout) };
        let interrupted = io::Error::last_os_error().raw_os_error() == Some(libc::EINTR);
        if (ready < 0 && !interrupted) || poll.revents & (libc::POLLERR | libc::POLLNVAL) != 0 {
            self.root = None;
        }
    }
}

// Returns true while at least one direct/reparented child is still live. The
// runner is a dedicated single-threaded process and has no unrelated children.
pub(crate) fn reap(root_pid: u32, state: &mut WaitState) -> io::Result<bool> {
    for _ in 0..REAP_BATCH {
        let mut status = 0;
        // SAFETY: valid status pointer; no signal handler or other thread waits.
        let pid = unsafe { libc::waitpid(-1, &mut status, libc::WNOHANG) };
        if pid > 0 {
            state.record_exit(root_pid, pid, status);
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
    // A busy producer of exited children must not starve the caller's deadline
    // or cancellation checks. EINTR consumes the same bounded operation budget.
    Ok(true)
}

fn kill_owned_children(root_pid: u32, state: &mut WaitState) -> io::Result<()> {
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
        .take(REAP_BATCH)
    {
        let child = std::str::from_utf8(token)
            .ok()
            .and_then(|token| token.parse::<i32>().ok())
            .filter(|child| *child > 0)
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidData, "invalid owned child list")
            })?;
        // Recheck immediately before sending a signal. kill() can report success
        // for an already waitable zombie, which supplies no live-stop evidence.
        let mut status = 0;
        // SAFETY: positive direct child, valid status pointer, sole reaper.
        match unsafe { libc::waitpid(child, &mut status, libc::WNOHANG) } {
            pid if pid > 0 => {
                state.record_exit(root_pid, pid, status);
                continue;
            }
            0 => {}
            _ => {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(libc::EINTR) {
                    continue;
                }
                return Err(error);
            }
        }
        // Every listed PID is our unreaped direct child. With SIGCHLD default
        // and this sole wait thread, it cannot be reused until a later reap.
        // Killing parents adopts even setsid/double-fork descendants; repeat.
        // SAFETY: positive PID obtained from the kernel's own-child list.
        if unsafe { libc::kill(child, libc::SIGKILL) } < 0 {
            let error = io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(error);
            }
        } else {
            state.pending_owner_kills.insert(child);
        }
    }
    Ok(())
}

pub(crate) fn cleanup_tree(root_pid: u32, state: &mut WaitState) -> bool {
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
        if kill_owned_children(root_pid, state).is_err() {
            return false;
        }
        std::thread::sleep(POLL_INTERVAL);
    }
}

fn invalid_measurement(root_pid: Option<u32>, reason: &str) -> Value {
    json!({"schema":1,"authority":"diagnostic_only","metric":"bb_entry_app_instructions",
        "integrity_verified":false,"measurement_contract":durante::report::measurement_contract(),
        "rep_telemetry_status":"unavailable",
        "scope":"process_tree_user_code","valid":false,"complete":false,
        "root_pid":root_pid,"reason":reason})
}

pub(crate) fn trusted_installation(path: &Path, executable: bool) -> io::Result<PathBuf> {
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
    protect_supervisor().map_err(|_| "cannot protect supervisor process access".to_owned())?;
    let mut output_options = OpenOptions::new();
    output_options
        .write(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK);
    if !options.report_existing {
        output_options.create_new(true);
    }
    let mut output = output_options.open(&options.report).map_err(|_| {
        "cannot create a new report file (existing paths are never replaced)".to_owned()
    })?;
    verify_report_path(&output, &options.report, 0)
        .map_err(|_| "report must be a new or explicitly supplied empty private file".to_owned())?;
    // Under meter protection the client refuses any descriptor for the report.
    let report_identity = output
        .metadata()
        .map(|m| (m.dev(), m.ino()))
        .map_err(|_| "cannot inspect report file".to_owned())?;
    let started = Instant::now();
    let mut result = json!({"schema":1,"authority":"diagnostic_only",
        "integrity_verified":false,"measurement_contract":durante::report::measurement_contract(),
        "security":{"counter_integrity":"application_mutable","isolation":"external_required"},
        "execution":{"state":"launch_failed","root_pid":null,"exit_code":null,
            "signal":null,"cancel_signal":null,"wall_time_ms":0,"cleanup_time_ms":0,
            "cleanup_complete":true,"records_cleanup_complete":true,
            "supervisor_reaped_processes":0},
        "measurement":invalid_measurement(None,"measurement did not start")});
    if !options.disallowed_cpu_features.is_empty() {
        result["disallowed_cpu_features"] = json!(options.disallowed_cpu_features);
    }
    if let Some(profile) = &options.cpuid_profile {
        result["cpuid_profile"] = json!(profile);
    }
    if options.latency_bound {
        result["latency_bound_version"] = json!(1);
    }
    if options.memory_operand_bytes {
        result["memory_operand_bytes_version"] = json!(1);
    }
    if let Some(limit) = options.work_budget {
        result["work_budget"] = work_budget::unavailable(limit);
    }
    if options.protect_meter {
        result["meter_protection"] = json!({"version": 1});
        result["security"]["counter_integrity"] = json!("protected_in_process_v1");
    }
    let outcome = execute(&options, drrun, client, report_identity, &mut result);
    result["execution"]["wall_time_ms"] =
        json!(started.elapsed().as_millis().min(u64::MAX as u128) as u64);
    let exit = match outcome {
        Ok(code) => code,
        Err(reason) => {
            result["measurement"] = invalid_measurement(None, reason);
            1
        }
    };
    verify_report_path(&output, &options.report, 0)
        .map_err(|_| "final report path was replaced or linked".to_owned())?;
    serde_json::to_writer_pretty(&mut output, &result)
        .map_err(|_| "cannot write report".to_owned())?;
    writeln!(output)
        .and_then(|()| output.sync_data())
        .map_err(|_| "cannot finish report".to_owned())?;
    let written = output
        .stream_position()
        .map_err(|_| "cannot verify report length".to_owned())?;
    verify_report_path(&output, &options.report, written)
        .map_err(|_| "final report path changed during publication".to_owned())?;
    Ok(exit)
}

pub(crate) fn verify_report_path(
    output: &File,
    path: &Path,
    expected_length: u64,
) -> io::Result<()> {
    let held = output.metadata()?;
    let named = fs::symlink_metadata(path)?;
    if !named.is_file()
        || held.dev() != named.dev()
        || held.ino() != named.ino()
        || held.nlink() != 1
        || named.nlink() != 1
        || held.len() != expected_length
        || named.len() != expected_length
        || named.permissions().mode() & 0o077 != 0
    {
        return Err(io::Error::other("unsafe report publication"));
    }
    Ok(())
}

pub(crate) fn cancellation_signal() -> i32 {
    CANCEL_SIGNAL.load(Ordering::Relaxed)
}

/// Enabled runs require the client's exact capability receipt. A legacy client
/// or a different policy must not silently satisfy the requested restriction.
pub(crate) fn policy_matches(report: &Value, expected: &[String]) -> bool {
    match report.get("disallowed_cpu_features") {
        None => expected.is_empty(),
        Some(value) => !expected.is_empty() && *value == json!(expected),
    }
}

/// The client must confirm the requested virtual processor in every image. A
/// legacy client, host CPUID or another profile cannot satisfy the request.
pub(crate) fn cpuid_profile_matches(report: &Value, expected: Option<&str>) -> bool {
    match (report.get("cpuid_profile"), expected) {
        (None, None) => true,
        (Some(value), Some(expected)) => value == expected,
        _ => false,
    }
}

/// A protected run requires the client's protection receipt in every image; an
/// unprotected run must not carry one.
pub(crate) fn meter_protection_matches(report: &Value, requested: bool) -> bool {
    match report.get("meter_protection") {
        None => !requested,
        Some(value) => requested && *value == json!({"version": 1}),
    }
}

/// Each optional counter must match the version and units requested by the caller.
pub(crate) fn counter_matches(report: &Value, name: &str, unit: &str, requested: bool) -> bool {
    counter_value(report, name, unit, requested).is_some()
}

/// An absent and unrequested field contributes zero only to local validation sums.
pub(crate) fn counter_value(
    report: &Value,
    name: &str,
    unit: &str,
    requested: bool,
) -> Option<u64> {
    match report.get(name) {
        None => (!requested).then_some(0),
        Some(value) if requested && value.as_object()?.len() == 2 && value["version"] == 1 => {
            value[unit].as_u64()
        }
        Some(_) => None,
    }
}

fn execute(
    options: &Options,
    drrun: PathBuf,
    client: PathBuf,
    report_identity: (u64, u64),
    result: &mut Value,
) -> Result<i32, &'static str> {
    let mut records = Records::create(options.records_dir.as_deref(), options.keep_records)
        .map_err(|_| "cannot create a fresh private records directory")?;
    if records.keep {
        result["execution"]["records_dir"] = json!(records.path);
    }
    let mut clock = options
        .work_budget
        .map(|_| Clock::create())
        .transpose()
        .map_err(|_| "cannot create the live progress file")?;
    let _owner = ProcessOwner::new().map_err(|_| "cannot establish process-tree ownership")?;
    let parent_pid = std::process::id() as libc::pid_t;
    let mut command = Command::new(drrun);
    command.args([
        "-quiet",
        "-follow_children",
        // The pinned trace path fails JVM lifecycle workloads. Execute
        // the qualified basic-block path without trace construction.
        "-disable_traces",
    ]);
    if options.protect_meter {
        // All runtime memory, heap included, lives in one reachable
        // reservation inside the window; nothing may fall back outside it.
        command.args([
            "-vm_size",
            "512M",
            "-reachable_heap",
            "-durante_window",
            METER_WINDOW,
        ]);
    } else {
        command.args(["-vm_size", "32M", "-vmheap_size", "32M"]);
    }
    command
        .arg("-c")
        .arg(client)
        .arg("-outdir")
        .arg(&records.path)
        .arg("-rep_bytes");
    if let Some(clock) = &clock {
        command.arg("-live_work_v1").arg(&clock.path);
    }
    for feature in &options.disallowed_cpu_features {
        command.args(["-disallow_cpu_feature", feature]);
    }
    if let Some(profile) = &options.cpuid_profile {
        command.args(["-cpuid_profile", profile]);
    }
    if options.latency_bound {
        command.arg("-latency_bound");
    }
    if options.memory_operand_bytes {
        command.arg("-memory_operand_bytes");
    }
    if options.protect_meter {
        command
            .arg("-protect_meter")
            .arg("-protect_file")
            .arg(format!("{}:{}", report_identity.0, report_identity.1));
    }
    command
        .arg("--")
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
            if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0 {
                return Err(io::Error::last_os_error());
            }
            // Mark, do not close: Rust's exec-error pipe must remain usable
            // until exec. On unsupported kernels fail closed rather than leak
            // a caller's non-CLOEXEC secrets into the measured application.
            if libc::syscall(libc::SYS_close_range, 3_u32, u32::MAX, 4_u32) < 0 {
                return Err(io::Error::last_os_error());
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
    let mut watch = RootWatch::new(root_pid);
    let mut crossing = None;
    let (state, mut code, mut forced) = loop {
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
        if let (Some(clock), Some(limit)) = (&mut clock, options.work_budget) {
            if let Some(observed) = clock.poll().filter(|p| p.exceeds(limit)) {
                crossing = Some(observed);
                break ("work_budget_exceeded", work_budget::EXIT_EXCEEDED, true);
            }
        }
        watch.pause(&waited, POLL_INTERVAL);
    };
    result["execution"]["state"] = json!(state);
    let cleanup_start = Instant::now();
    let cleanup_complete = !forced || cleanup_tree(root_pid, &mut waited);
    if crossing.is_some() {
        result["execution"]["work_budget_killed_processes"] = json!(waited.killed_during_cleanup);
        if let Some(signal) = waited.other_child_signal {
            result["execution"]["independent_child_signal"] = json!(signal);
        } else if cleanup_complete
            && waited.killed_during_cleanup == 0
            && cancellation_signal() == 0
        {
            // The tree finished between the crossing and the first effective
            // kill. Validate its terminal records to classify the natural exit.
            forced = false;
            code = 0;
            result["execution"]["state"] = json!("complete");
        }
    }
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
    // Re-read after reaping. Concurrent invalidation or an unavailable final
    // publication cannot turn a forced stop into usable budget evidence.
    let final_progress = cleanup_complete
        .then(|| clock.as_mut().and_then(Clock::poll))
        .flatten();
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
        if let (Some(observed), Some(limit)) = (crossing, options.work_budget) {
            let after_stop_signal = cancellation_signal();
            if after_stop_signal != 0 {
                result["execution"]["state"] = json!("cancelled");
                result["execution"]["cancel_signal"] = json!(after_stop_signal);
                code = 128 + after_stop_signal;
            } else if final_progress.is_some()
                && waited.killed_during_cleanup > 0
                && waited.other_child_signal.is_none()
                && waited.root_status.is_some()
            {
                result["work_budget"] = observed.receipt(limit, "live");
            } else {
                result["execution"]["state"] = json!("incomplete");
                code = 1;
            }
        }
    } else if !normal_exit {
        result["execution"]["state"] = json!("incomplete");
        result["measurement"] = invalid_measurement(Some(root_pid), "root did not exit normally");
        code = 1;
    } else {
        let mut measurement = durante::collect::collect(&records.path, root_pid);
        if measurement["valid"] == true
            && measurement["complete"] == true
            && !policy_matches(&measurement, &options.disallowed_cpu_features)
        {
            measurement = invalid_measurement(
                Some(root_pid),
                "client CPU feature policy does not match the request",
            );
        }
        if measurement["valid"] == true
            && measurement["complete"] == true
            && !cpuid_profile_matches(&measurement, options.cpuid_profile.as_deref())
        {
            measurement = invalid_measurement(
                Some(root_pid),
                "client CPUID profile does not match the request",
            );
        }
        if measurement["valid"] == true
            && measurement["complete"] == true
            && !counter_matches(
                &measurement,
                "latency_bound",
                "cycles_x16",
                options.latency_bound,
            )
        {
            measurement = invalid_measurement(
                Some(root_pid),
                "client latency bound does not match the request",
            );
        }
        if measurement["valid"] == true
            && measurement["complete"] == true
            && !counter_matches(
                &measurement,
                "memory_operand_bytes",
                "bytes",
                options.memory_operand_bytes,
            )
        {
            measurement = invalid_measurement(
                Some(root_pid),
                "client memory operand bytes do not match the request",
            );
        }
        if measurement["valid"] == true
            && measurement["complete"] == true
            && !meter_protection_matches(&measurement, options.protect_meter)
        {
            measurement = invalid_measurement(
                Some(root_pid),
                "client meter protection does not match the request",
            );
        }
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
        if let Some(limit) = options.work_budget {
            if measurement["valid"] == true && measurement["complete"] == true {
                match final_progress.filter(|p| p.matches_terminal(&measurement)) {
                    Some(observed) => {
                        result["work_budget"] = observed.receipt(limit, "terminal");
                        if observed.exceeds(limit) {
                            result["execution"]["state"] = json!("work_budget_exceeded");
                            code = work_budget::EXIT_EXCEEDED;
                        }
                    }
                    None => {
                        measurement = invalid_measurement(
                            Some(root_pid),
                            "live work progress is unavailable or disagrees with terminal records",
                        )
                    }
                }
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
    let clock_cleaned = clock.as_mut().is_none_or(|clock| {
        if !cleanup_complete {
            clock.directory.keep = true;
        }
        let ok = clock.directory.cleanup().is_ok();
        if !ok || !cleanup_complete {
            result["execution"]["progress_dir"] = json!(clock.directory.path);
        }
        ok
    });
    if records.cleanup().is_err() || !clock_cleaned {
        result["execution"]["state"] = json!("cleanup_failed");
        result["execution"]["records_cleanup_complete"] = json!(false);
        if let Some(limit) = options.work_budget {
            result["work_budget"] = work_budget::unavailable(limit);
        }
        result["execution"]["records_dir"] = json!(records.path);
        result["measurement"] =
            invalid_measurement(Some(root_pid), "record-directory cleanup failed");
        code = 1;
    }
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cleanup_requires_a_matching_signal_for_each_effective_kill() {
        let mut waited = WaitState::default();
        // A completed root can race the cleanup pass even after kill() succeeds.
        waited.pending_owner_kills.insert(42);
        waited.record_exit(42, 42, 0);
        assert_eq!(waited.root_status, Some(0));
        assert_eq!(waited.killed_during_cleanup, 0);
        assert!(waited.pending_owner_kills.is_empty());
        assert!(waited.other_child_signal.is_none());

        // An owned descendant stopped during cleanup still counts for the tree.
        waited.pending_owner_kills.insert(43);
        waited.record_exit(42, 43, libc::SIGKILL);
        assert_eq!(waited.killed_during_cleanup, 1);
        assert!(waited.other_child_signal.is_none());

        // The same numeric PID can later be reused by another descendant. A
        // SIGKILL the owner never sent is an independent cause in either role.
        waited.record_exit(42, 43, libc::SIGKILL);
        assert_eq!(waited.killed_during_cleanup, 1);
        assert_eq!(waited.other_child_signal, Some(libc::SIGKILL));
    }

    #[test]
    fn root_watch_ends_the_pause_when_the_root_exits() {
        let mut child = Command::new("/bin/sleep").arg("0.05").spawn().unwrap();
        let mut watch = RootWatch::new(child.id());
        if watch.root.is_none() {
            // No pidfd on this kernel: the watch is interval polling only.
            child.wait().unwrap();
            return;
        }
        // Far longer than the child lives: only its exit can end this pause.
        let started = Instant::now();
        watch.pause(&WaitState::default(), Duration::from_secs(30));
        assert!(started.elapsed() < Duration::from_secs(15));
        assert!(watch.root.is_some());
        // An exited but unreaped root keeps ending the pause at once.
        let started = Instant::now();
        watch.pause(&WaitState::default(), Duration::from_secs(30));
        assert!(started.elapsed() < Duration::from_secs(15));
        child.wait().unwrap();
        // Once the root is reaped the watch only sleeps.
        let reaped = WaitState {
            root_status: Some(0),
            ..WaitState::default()
        };
        let started = Instant::now();
        watch.pause(&reaped, Duration::from_millis(40));
        assert!(started.elapsed() >= Duration::from_millis(40));
        assert!(watch.root.is_none());
    }

    #[test]
    fn root_watch_waits_out_the_interval_while_the_root_runs() {
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let mut watch = RootWatch::new(child.id());
        let started = Instant::now();
        watch.pause(&WaitState::default(), Duration::from_millis(40));
        let elapsed = started.elapsed();
        child.kill().unwrap();
        child.wait().unwrap();
        assert!(elapsed >= Duration::from_millis(40));
    }

    #[test]
    fn raw_signal_wins_even_after_sending_an_owner_kill() {
        let mut waited = WaitState::default();
        waited.pending_owner_kills.insert(42);
        waited.record_exit(42, 42, libc::SIGXCPU);
        assert_eq!(waited.killed_during_cleanup, 0);
        assert_eq!(waited.other_child_signal, Some(libc::SIGXCPU));
        assert!(waited.pending_owner_kills.is_empty());
    }
}
