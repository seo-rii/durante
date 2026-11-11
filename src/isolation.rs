//! Opt-in kernel namespace execution. Counters remain application-mutable.
use crate::runner;
use serde::Deserialize;
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, BTreeSet};
use std::ffi::{CString, OsString};
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{self, Read, Seek, Write};
use std::os::fd::{AsRawFd, FromRawFd, RawFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

const SOURCE_SHA: &str = "9760d007363e3abba7c747489910f9f82d9fca53ba3bd3282e396fa3c97a3314";
const PATCH_SHA: &str = "624bf41de9308836fed6434a656ea54c8aae4765e710ee557f95330523a5eb97";
const SOURCE_URL: &str =
    "https://github.com/containers/bubblewrap/releases/download/v0.12.0/bubblewrap-0.12.0.tar.xz";
const MAX_FILES: usize = 256;
const MAX_BYTES: u64 = 64 << 20;
const MAX_REPORT: u64 = 16 << 20;
const PROFILE: &str = "bubblewrap-0.12-durante-pid1-v1";

pub struct Options {
    pub drrun: PathBuf,
    pub client: PathBuf,
    pub report: PathBuf,
    pub workspace: PathBuf,
    pub launcher: PathBuf,
    pub timeout_ms: u64,
    pub command: Vec<OsString>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    schema: u64,
    inputs: BuildInputs,
    binary_sha256: String,
}
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BuildInputs {
    version: String,
    source_url: String,
    source_sha256: String,
    patch_sha256: String,
    recipe: u64,
    meson_options: Vec<String>,
}

fn problem(message: &'static str) -> io::Error {
    io::Error::other(message)
}

fn verify_stdio_fd(fd: RawFd) -> io::Result<()> {
    let mut info = std::mem::MaybeUninit::<libc::stat>::uninit();
    let mut filesystem = std::mem::MaybeUninit::<libc::statfs>::uninit();
    // SAFETY: valid output buffers; these operations do not take ownership.
    if unsafe { libc::fstat(fd, info.as_mut_ptr()) } < 0
        || unsafe { libc::fstatfs(fd, filesystem.as_mut_ptr()) } < 0
    {
        return Err(problem("isolation requires open ordinary stdio handles"));
    }
    let info = unsafe { info.assume_init() };
    let filesystem = unsafe { filesystem.assume_init() };
    if [
        0x9fa0,
        0x6265_6572,
        0x6367_7270,
        0x0027_e0eb,
        0x0904_1934,
        0x5049_4446,
        0x7363_6673,
        0x6462_6720,
        0x7472_6163,
        0x6265_6570,
    ]
    .contains(&filesystem.f_type)
    {
        return Err(problem(
            "stdio must not expose proc, sysfs or cgroup control handles",
        ));
    }
    let kind = info.st_mode & libc::S_IFMT;
    if kind == libc::S_IFREG || kind == libc::S_IFIFO {
        return Ok(());
    }
    if kind == libc::S_IFCHR {
        if [
            libc::makedev(1, 3),
            libc::makedev(1, 5),
            libc::makedev(1, 8),
            libc::makedev(1, 9),
        ]
        .contains(&info.st_rdev)
        {
            return Ok(());
        }
    }
    Err(problem(
        "stdio cannot carry directory, socket or arbitrary device capabilities",
    ))
}

fn safe_file(path: &Path, maximum: u64, executable: bool) -> io::Result<File> {
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)?;
    let info = file.metadata()?;
    if !info.is_file()
        || info.nlink() != 1
        || info.len() > maximum
        || info.mode() & 0o6022 != 0
        || (executable && info.mode() & 0o111 == 0)
    {
        return Err(problem("unsafe or oversized installation file"));
    }
    Ok(file)
}

fn read_bounded(path: &Path, maximum: u64) -> io::Result<Vec<u8>> {
    let file = safe_file(path, maximum, false)?;
    let before = file.metadata()?;
    let mut data = Vec::new();
    (&file).take(maximum + 1).read_to_end(&mut data)?;
    let after = file.metadata()?;
    if data.len() as u64 != before.len() || !unchanged(&before, &after) {
        return Err(problem("installation file changed while reading"));
    }
    Ok(data)
}

fn unchanged(a: &fs::Metadata, b: &fs::Metadata) -> bool {
    a.dev() == b.dev()
        && a.ino() == b.ino()
        && a.len() == b.len()
        && a.mtime() == b.mtime()
        && a.mtime_nsec() == b.mtime_nsec()
        && a.ctime() == b.ctime()
        && a.ctime_nsec() == b.ctime_nsec()
        && b.nlink() == 1
}

fn verify_launcher(path: &Path) -> io::Result<PathBuf> {
    let marker = path.with_file_name(format!(
        "{}.durante-isolation.json",
        path.file_name()
            .and_then(|name| name.to_str())
            .ok_or_else(|| problem("invalid launcher filename"))?
    ));
    let manifest: Manifest = serde_json::from_slice(&read_bounded(&marker, 65536)?)
        .map_err(|_| problem("invalid isolation manifest"))?;
    let expected = [
        "--buildtype=release",
        "-Dtests=false",
        "-Dman=disabled",
        "-Dselinux=disabled",
        "-Dbash_completion=disabled",
        "-Dzsh_completion=disabled",
    ];
    if manifest.schema != 1
        || manifest.inputs.version != "0.12.0"
        || manifest.inputs.source_url != SOURCE_URL
        || manifest.inputs.source_sha256 != SOURCE_SHA
        || manifest.inputs.patch_sha256 != PATCH_SHA
        || manifest.inputs.recipe != 1
        || manifest.inputs.meson_options != expected
    {
        return Err(problem(
            "isolation manifest does not match the reviewed profile",
        ));
    }
    let mut file = safe_file(path, 16 << 20, true)?;
    let before = file.metadata()?;
    let mut hash = Sha256::new();
    io::copy(&mut file, &mut hash)?;
    if !unchanged(&before, &file.metadata()?)
        || format!("{:x}", hash.finalize()) != manifest.binary_sha256
    {
        return Err(problem("isolation launcher hash mismatch"));
    }
    fs::canonicalize(path)
}

fn directory(path: &Path) -> io::Result<File> {
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
}

fn child_open(parent: RawFd, name: &std::ffi::OsStr) -> io::Result<File> {
    let name = CString::new(name.as_bytes())?;
    // SAFETY: parent remains open and name is one readdir-provided leaf.
    let fd = unsafe {
        libc::openat(
            parent,
            name.as_ptr(),
            libc::O_RDONLY | libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_CLOEXEC,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn children(file: &File) -> io::Result<fs::ReadDir> {
    fs::read_dir(format!("/proc/self/fd/{}", file.as_raw_fd()))
}

#[derive(Default)]
struct CopyBudget {
    entries: usize,
    bytes: u64,
}

fn copy_workspace(
    source: &File,
    destination: &Path,
    depth: usize,
    budget: &mut CopyBudget,
) -> io::Result<()> {
    if depth > 8 {
        return Err(problem("workspace nesting exceeds eight directories"));
    }
    for entry in children(source)? {
        let name = entry?.file_name();
        budget.entries += 1;
        if budget.entries > MAX_FILES {
            return Err(problem("workspace entry limit exceeded"));
        }
        let mut input = child_open(source.as_raw_fd(), &name)?;
        let before = input.metadata()?;
        let target = destination.join(&name);
        if before.is_dir() {
            DirBuilder::new().mode(0o700).create(&target)?;
            copy_workspace(&input, &target, depth + 1, budget)?;
        } else if before.is_file() && before.nlink() == 1 {
            budget.bytes = budget
                .bytes
                .checked_add(before.len())
                .ok_or_else(|| problem("workspace byte limit exceeded"))?;
            if budget.bytes > MAX_BYTES {
                return Err(problem("workspace byte limit exceeded"));
            }
            let mut output = OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&target)?;
            let copied = io::copy(&mut (&mut input).take(before.len() + 1), &mut output)?;
            if copied != before.len() || !unchanged(&before, &input.metadata()?) {
                return Err(problem("workspace file changed during snapshot"));
            }
            output.set_permissions(fs::Permissions::from_mode(if before.mode() & 0o111 != 0 {
                0o700
            } else {
                0o600
            }))?;
        } else {
            return Err(problem(
                "workspace permits only directories and single-link regular files",
            ));
        }
    }
    Ok(())
}

// A pre-existing, bounded cgroup namespace is an operator prerequisite. This
// code neither creates host cgroups nor trusts look-alike files on another FS.
fn resource_limits() -> io::Result<Value> {
    if fs::read("/proc/self/cgroup")? != b"0::/\n" {
        return Err(problem(
            "isolation requires a private bounded cgroup v2 namespace",
        ));
    }
    let root = directory(Path::new("/sys/fs/cgroup"))?;
    let mut st = std::mem::MaybeUninit::<libc::statfs>::uninit();
    // SAFETY: fstatfs initializes the provided structure on success.
    if unsafe { libc::fstatfs(root.as_raw_fd(), st.as_mut_ptr()) } != 0
        || unsafe { st.assume_init() }.f_type != 0x6367_7270
    {
        return Err(problem("resource control mount is not cgroup v2"));
    }
    let read = |name: &str| -> io::Result<String> {
        let file = child_open(root.as_raw_fd(), std::ffi::OsStr::new(name))?;
        let mut data = String::new();
        file.take(129).read_to_string(&mut data)?;
        if data.len() > 128 {
            return Err(problem("oversized cgroup limit"));
        }
        Ok(data)
    };
    parse_limits(&read("memory.max")?, &read("pids.max")?, &read("cpu.max")?)
}

fn parse_limits(memory: &str, pids: &str, cpu: &str) -> io::Result<Value> {
    let number = |value: &str| {
        value
            .trim()
            .parse::<u64>()
            .map_err(|_| problem("cgroup limits must be finite positive integers"))
    };
    let memory = number(memory)?;
    let pids = number(pids)?;
    let fields: Vec<_> = cpu.split_whitespace().collect();
    if fields.len() != 2 {
        return Err(problem("invalid cgroup CPU quota"));
    }
    let quota = number(fields[0])?;
    let period = number(fields[1])?;
    if memory == 0
        || memory > 4 << 30
        || pids == 0
        || pids > 256
        || quota == 0
        || period == 0
        || quota as u128 > period as u128 * 8
    {
        return Err(problem("cgroup limits exceed the isolation profile"));
    }
    Ok(json!({"memory_max_bytes":memory,"pids_max":pids,
        "cpu_quota_us":quota,"cpu_period_us":period}))
}

fn policy(path: &Path) -> io::Result<File> {
    // classic BPF: enforce AMD64, reject x32, then deny selected host-control
    // interfaces. Namespace/capability restrictions are independently required.
    let statement = |code, k| libc::sock_filter {
        code,
        jt: 0,
        jf: 0,
        k,
    };
    let jump = |code, k, jt, jf| libc::sock_filter { code, jt, jf, k };
    let mut rules = vec![
        statement(0x20, 4),
        jump(0x15, 0xc000_003e, 1, 0),
        statement(0x06, 0x8000_0000),
        statement(0x20, 0),
        jump(0x45, 0x4000_0000, 0, 1),
        statement(0x06, 0x8000_0000),
    ];
    let denied = [
        libc::SYS_ptrace,
        libc::SYS_process_vm_readv,
        libc::SYS_process_vm_writev,
        libc::SYS_pidfd_getfd,
        libc::SYS_mount,
        libc::SYS_umount2,
        libc::SYS_pivot_root,
        libc::SYS_setns,
        libc::SYS_unshare,
        libc::SYS_bpf,
        libc::SYS_perf_event_open,
        libc::SYS_userfaultfd,
        libc::SYS_fallocate,
        libc::SYS_io_uring_setup,
        libc::SYS_io_uring_register,
        libc::SYS_io_uring_enter,
        libc::SYS_io_setup,
        libc::SYS_io_submit,
        libc::SYS_io_cancel,
        libc::SYS_io_getevents,
        333, // io_pgetevents on AMD64 (not exposed by this libc crate).
        libc::SYS_socket,
        libc::SYS_socketpair,
        libc::SYS_keyctl,
        libc::SYS_add_key,
        libc::SYS_request_key,
    ];
    for syscall in denied {
        rules.push(jump(0x15, syscall as u32, 0, 1));
        rules.push(statement(0x06, 0x0005_0000 | libc::EPERM as u32));
    }
    rules.push(statement(0x06, 0x7fff_0000));
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(path)?;
    for rule in rules {
        output.write_all(&rule.code.to_ne_bytes())?;
        output.write_all(&[rule.jt, rule.jf])?;
        output.write_all(&rule.k.to_ne_bytes())?;
    }
    drop(output);
    File::open(path)
}

fn valid_inner(value: &Value) -> bool {
    let base = value["schema"] == 1
        && value["authority"] == "diagnostic_only"
        && value["security"]["counter_integrity"] == "application_mutable"
        && value["execution"]["state"] == "complete"
        && value["execution"]["cleanup_complete"] == true
        && value["execution"]["records_cleanup_complete"] == true
        && value["measurement"]["valid"] == true
        && value["measurement"]["complete"] == true
        && value["measurement"]["authority"] == "diagnostic_only"
        && value["measurement"]["instructions"]
            .as_u64()
            .is_some_and(|n| n > 0);
    if !base {
        return false;
    }
    let measurement = &value["measurement"];
    if measurement["schema"] != 1
        || measurement["metric"] != "bb_entry_app_instructions"
        || measurement["scope"] != "process_tree_user_code"
        || measurement.get("reason").is_some()
    {
        return false;
    }
    let Some(processes) = measurement["processes"].as_array() else {
        return false;
    };
    let Some(segments) = measurement["segments"].as_array() else {
        return false;
    };
    if processes.is_empty()
        || processes.len() > MAX_FILES
        || segments.is_empty()
        || segments.len() > durante::collect::MAX_EVENTS * MAX_FILES
        || measurement["process_count"].as_u64() != Some(processes.len() as u64)
        || measurement["image_count"].as_u64() != Some(segments.len() as u64)
    {
        return false;
    }
    const FIELDS: [&str; 5] = [
        "instructions",
        "rep_bytes",
        "rep_entries",
        "unsupported_rep_entries",
        "signals_observed",
    ];
    let mut ids = BTreeSet::new();
    let mut sums = BTreeMap::new();
    for process in processes {
        let Some(pid) = process["pid"]
            .as_u64()
            .filter(|pid| *pid > 0 && *pid <= i32::MAX as u64)
        else {
            return false;
        };
        if !ids.insert(pid) || process["exit_code"].as_u64().is_none_or(|code| code > 255) {
            return false;
        }
        sums.insert(pid, ([0_u64; 5], 0_u64));
    }
    if !measurement["root_pid"]
        .as_u64()
        .is_some_and(|pid| ids.contains(&pid))
    {
        return false;
    }
    let mut totals = [0_u64; 5];
    for segment in segments {
        let Some(pid) = segment["pid"].as_u64() else {
            return false;
        };
        let Some((sums, count)) = sums.get_mut(&pid) else {
            return false;
        };
        *count += 1;
        for (index, key) in FIELDS.iter().enumerate() {
            let Some(value) = segment[key].as_u64() else {
                return false;
            };
            let Some(sum) = sums[index].checked_add(value) else {
                return false;
            };
            sums[index] = sum;
            let Some(sum) = totals[index].checked_add(value) else {
                return false;
            };
            totals[index] = sum;
        }
    }
    for process in processes {
        let (sums, count) = &sums[&process["pid"].as_u64().unwrap()];
        if process["images"].as_u64() != Some(*count) {
            return false;
        }
        for (index, key) in FIELDS.iter().enumerate() {
            if process[key].as_u64() != Some(sums[index]) {
                return false;
            }
        }
    }
    let Some(rep) = measurement["rep_bytes_enabled"].as_bool() else {
        return false;
    };
    if (!rep && totals[2] != 0) || (totals[2] == 0 && totals[1] != 0) {
        return false;
    }
    if totals[3] != 0
        || measurement["quality"]
            != if totals[4] == 0 {
                "uninterrupted"
            } else {
                "signals_observed"
            }
    {
        return false;
    }
    FIELDS
        .iter()
        .enumerate()
        .all(|(index, key)| measurement[key].as_u64() == Some(totals[index]))
}

fn cleanup_tree_bounded(root: &Path, depth: usize, entries: &mut usize) -> io::Result<()> {
    if depth > 9 {
        return Err(problem("unexpected cleanup depth"));
    }
    let held = directory(root)?;
    for entry in children(&held)? {
        let entry = entry?;
        *entries += 1;
        if *entries > MAX_FILES + 4 {
            return Err(problem("unexpected cleanup contents"));
        }
        let name = entry.file_name();
        let path = root.join(&name);
        if entry.file_type()?.is_dir() {
            cleanup_tree_bounded(&path, depth + 1, entries)?;
        } else {
            let name = CString::new(name.as_bytes())?;
            // SAFETY: unlink one leaf through the held original directory FD.
            if unsafe { libc::unlinkat(held.as_raw_fd(), name.as_ptr(), 0) } < 0 {
                return Err(io::Error::last_os_error());
            }
        }
    }
    fs::remove_dir(root)
}

pub fn run(options: Options) -> Result<i32, String> {
    if options.command.is_empty() || !(1..=86_400_000).contains(&options.timeout_ms) {
        return Err("target command and bounded timeout required".into());
    }
    for fd in 0..=2 {
        verify_stdio_fd(fd)
            .map_err(|_| "isolation requires ordinary safe stdin/stdout/stderr handles")?;
    }
    let launcher = verify_launcher(&options.launcher)
        .map_err(|_| "verified patched isolation launcher required")?;
    let drrun = runner::trusted_installation(&options.drrun, true)
        .map_err(|_| "trusted SDK launcher required")?;
    if drrun.file_name() != Some(std::ffi::OsStr::new("drrun"))
        || drrun.parent().and_then(Path::file_name) != Some(std::ffi::OsStr::new("bin64"))
    {
        return Err("isolation requires the SDK bin64/drrun layout".into());
    }
    let sdk = drrun
        .parent()
        .and_then(Path::parent)
        .ok_or("invalid SDK layout")?;
    let client = runner::trusted_installation(&options.client, false)
        .map_err(|_| "trusted client required")?;
    let current = runner::trusted_installation(
        &std::env::current_exe().map_err(|_| "cannot locate CLI")?,
        true,
    )
    .map_err(|_| "trusted CLI required")?;
    let resources = resource_limits().map_err(|_| {
        "a finite cgroup v2 namespace is required (memory<=4GiB, pids<=256, CPU<=8 cores)"
    })?;
    runner::protect_supervisor().map_err(|_| "cannot protect supervisor")?;
    let mut report = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&options.report)
        .map_err(|_| "final report must be a fresh private file")?;
    let scratch =
        runner::Records::create(None, true).map_err(|_| "cannot create private isolation state")?;
    let work = scratch.path.join("work");
    let capture = scratch.path.join("inner.json");
    let policy_path = scratch.path.join("policy.bpf");
    let started = Instant::now();
    let mut result = json!({"schema":1,"authority":"diagnostic_only", "security":{
        "counter_integrity":"application_mutable","isolation":PROFILE,
        "external_resource_limits_required":true,"cgroup_limits":resources},
        "execution":{"state":"launch_failed","wrapper_exit_code":null,
        "cleanup_complete":true,"scratch_cleanup_complete":true,"wall_time_ms":0}});
    let outcome = execute(
        &options,
        &launcher,
        &drrun,
        sdk,
        &client,
        &current,
        &scratch.path,
        &work,
        &capture,
        &policy_path,
        &mut result,
    );
    let mut code = match outcome {
        Ok(code) => code,
        Err(message) => {
            result["execution"]["reason"] = json!(message);
            if result["execution"]["state"] == "complete" {
                result["execution"]["state"] = json!("incomplete");
            }
            1
        }
    };
    result["execution"]["wall_time_ms"] = json!(started.elapsed().as_millis() as u64);
    if result["execution"]["cleanup_complete"] != true
        || cleanup_tree_bounded(&scratch.path, 0, &mut 0).is_err()
    {
        result["execution"]["scratch_cleanup_complete"] = json!(false);
        result["execution"]["state"] = json!("cleanup_failed");
        result["execution"]["retained_scratch"] = json!(scratch.path);
        result
            .as_object_mut()
            .unwrap()
            .remove("untrusted_inner_report");
        code = 1;
    }
    runner::verify_report_path(&report, &options.report, 0)
        .map_err(|_| "final report was modified")?;
    serde_json::to_writer_pretty(&mut report, &result).map_err(|_| "cannot write final report")?;
    writeln!(report)
        .and_then(|()| report.sync_data())
        .map_err(|_| "cannot finish final report")?;
    let length = report
        .stream_position()
        .map_err(|_| "cannot verify final report")?;
    runner::verify_report_path(&report, &options.report, length)
        .map_err(|_| "final report changed")?;
    Ok(code)
}

#[allow(clippy::too_many_arguments)]
fn execute(
    options: &Options,
    launcher: &Path,
    drrun: &Path,
    sdk: &Path,
    client: &Path,
    current: &Path,
    scratch: &Path,
    work: &Path,
    capture: &Path,
    policy_path: &Path,
    result: &mut Value,
) -> Result<i32, &'static str> {
    DirBuilder::new()
        .mode(0o700)
        .create(work)
        .map_err(|_| "cannot create workspace snapshot")?;
    let source = directory(&options.workspace).map_err(|_| "workspace must be a real directory")?;
    copy_workspace(&source, work, 0, &mut CopyBudget::default())
        .map_err(|_| "workspace snapshot rejected unsafe, changing or oversized input")?;
    OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .open(capture)
        .map_err(|_| "cannot create private capture file")?;
    let filter = policy(policy_path).map_err(|_| "cannot prepare isolation policy")?;
    let _owner = runner::ProcessOwner::new().map_err(|_| "cannot own isolation process tree")?;
    let mut command = Command::new(launcher);
    command.env_clear().args([
        "--unshare-user",
        "--unshare-pid",
        "--unshare-net",
        "--unshare-ipc",
        "--unshare-uts",
        "--disable-userns",
        "--assert-userns-disabled",
        "--cap-drop",
        "ALL",
        "--new-session",
        "--die-with-parent",
        "--hostname",
        "durante",
        "--clearenv",
        "--setenv",
        "PATH",
        "/usr/bin:/bin",
        "--setenv",
        "LANG",
        "C",
        "--setenv",
        "LC_ALL",
        "C",
        "--setenv",
        "HOME",
        "/tmp",
        "--setenv",
        "TMPDIR",
        "/tmp",
        "--setenv",
        "DYNAMORIO_CONFIGDIR",
        "/tmp",
    ]);
    for system in ["/usr/bin", "/usr/lib", "/usr/lib64"] {
        if Path::new(system).is_dir() {
            command.args(["--ro-bind", system, system]);
        }
    }
    command.args([
        "--symlink",
        "usr/bin",
        "/bin",
        "--symlink",
        "usr/lib",
        "/lib",
        "--symlink",
        "usr/lib64",
        "/lib64",
        "--proc",
        "/proc",
        "--dev",
        "/dev",
        "--size",
        "134217728",
        "--tmpfs",
        "/tmp",
        "--dir",
        "/capture",
    ]);
    for system in ["/etc/ld.so.cache", "/etc/localtime"] {
        if let Ok(info) = fs::symlink_metadata(system) {
            if info.is_file() {
                command.args(["--ro-bind", system, system]);
            }
        }
    }
    command
        .arg("--ro-bind")
        .arg(sdk)
        .arg(sdk)
        .arg("--ro-bind")
        .arg(client)
        .arg(client)
        .arg("--ro-bind")
        .arg(current)
        .arg("/opt/durante/durante")
        .arg("--ro-bind")
        .arg(work)
        .arg("/work")
        .arg("--bind")
        .arg(capture)
        .arg("/capture/report.json")
        .args([
            "--remount-ro",
            "/proc",
            "--remount-ro",
            "/dev",
            "--remount-ro",
            "/",
            "--seccomp",
        ])
        .arg(filter.as_raw_fd().to_string())
        .args([
            "--chdir",
            "/work",
            "--",
            "/opt/durante/durante",
            "run",
            "--drrun",
        ])
        .arg(drrun)
        .arg("--client")
        .arg(client)
        .args([
            "--report",
            "/capture/report.json",
            "--report-existing",
            "--timeout-ms",
        ])
        .arg(options.timeout_ms.to_string())
        .arg("--")
        .args(&options.command)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit())
        .process_group(0);
    let parent = std::process::id() as libc::pid_t;
    let policy_fd = filter.as_raw_fd();
    let cpu = options.timeout_ms.div_ceil(1000) + 1;
    // SAFETY: only async-signal-safe immediate libc operations are used here.
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
            if libc::prctl(libc::PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) < 0
                || libc::syscall(libc::SYS_close_range, 3_u32, u32::MAX, 4_u32) < 0
                || libc::fcntl(policy_fd, libc::F_SETFD, 0) < 0
                || libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGKILL, 0, 0, 0) < 0
                || libc::getppid() != parent
            {
                return Err(io::Error::last_os_error());
            }
            for (kind, value) in [
                (libc::RLIMIT_CORE, 0),
                (libc::RLIMIT_FSIZE, MAX_REPORT),
                (libc::RLIMIT_AS, 1 << 30),
                (libc::RLIMIT_CPU, cpu),
                (libc::RLIMIT_NOFILE, 1024),
            ] {
                let limit = libc::rlimit {
                    rlim_cur: value,
                    rlim_max: value,
                };
                if libc::setrlimit(kind, &limit) < 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            Ok(())
        });
    }
    if runner::cancellation_signal() != 0 {
        return Err("isolation cancelled before launch");
    }
    let child = command
        .spawn()
        .map_err(|_| "isolation launch failed; no unisolated fallback")?;
    let pid = child.id();
    drop(child);
    result["execution"]["wrapper_pid"] = json!(pid);
    let deadline = Instant::now() + Duration::from_millis(options.timeout_ms);
    let mut waited = runner::WaitState::default();
    let (state, mut code, forced) = loop {
        let signal = runner::cancellation_signal();
        if signal != 0 {
            break ("cancelled", 128 + signal, true);
        }
        match runner::reap(pid, &mut waited) {
            Ok(false) => break ("complete", 0, false),
            Err(_) => break ("wait_failed", 1, true),
            Ok(true) => {}
        }
        if Instant::now() >= deadline {
            break ("timed_out", 124, true);
        }
        std::thread::sleep(runner::POLL_INTERVAL);
    };
    let cleaned = !forced || runner::cleanup_tree(pid, &mut waited);
    result["execution"]["state"] = json!(state);
    result["execution"]["cleanup_complete"] = json!(cleaned);
    result["execution"]["supervisor_reaped_processes"] = json!(waited.reaped);
    let success = waited
        .root_status
        .is_some_and(|status| libc::WIFEXITED(status) && libc::WEXITSTATUS(status) == 0);
    if let Some(status) = waited.root_status {
        if libc::WIFEXITED(status) {
            result["execution"]["wrapper_exit_code"] = json!(libc::WEXITSTATUS(status));
        } else if libc::WIFSIGNALED(status) {
            result["execution"]["wrapper_signal"] = json!(libc::WTERMSIG(status));
        }
    }
    if !cleaned {
        result["execution"]["state"] = json!("cleanup_failed");
        return Ok(1);
    }
    if forced {
        return Ok(code);
    }
    if !success {
        result["execution"]["state"] = json!("isolation_failed");
        return Ok(1);
    }
    let data = read_bounded(capture, MAX_REPORT)
        .map_err(|_| "inner report missing, unsafe or oversized")?;
    let inner: Value =
        serde_json::from_slice(&data).map_err(|_| "invalid untrusted inner report")?;
    if valid_inner(&inner) {
        result["untrusted_inner_report"] = inner;
    } else {
        result["execution"]["state"] = json!("incomplete");
        code = 1;
    }
    let _ = scratch; // Kept outside the guest; never mounted as a directory.
    Ok(code)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn resource_profile_rejects_unbounded_or_excessive_controls() {
        assert!(parse_limits("1073741824\n", "64\n", "100000 100000\n").is_ok());
        for (memory, pids, cpu) in [
            ("max", "64", "100000 100000"),
            ("1", "max", "100000 100000"),
            ("1", "64", "max 100000"),
            ("1", "257", "100000 100000"),
            ("1", "64", "900000 100000"),
        ] {
            assert!(parse_limits(memory, pids, cpu).is_err());
        }
    }
    #[test]
    fn workspace_snapshot_rejects_symlinks_and_hardlinks() {
        for link in [false, true] {
            let root = tempfile::tempdir().unwrap();
            let src = root.path().join("source");
            let dst = root.path().join("target");
            fs::create_dir(&src).unwrap();
            fs::create_dir(&dst).unwrap();
            let victim = root.path().join("victim");
            fs::write(&victim, b"outside").unwrap();
            if link {
                fs::hard_link(&victim, src.join("file")).unwrap();
            } else {
                std::os::unix::fs::symlink(&victim, src.join("file")).unwrap();
            }
            assert!(copy_workspace(
                &directory(&src).unwrap(),
                &dst,
                0,
                &mut CopyBudget::default()
            )
            .is_err());
            assert_eq!(fs::read(victim).unwrap(), b"outside");
        }
    }

    #[test]
    fn inner_report_validation_rejects_partial_and_overflowing_sums() {
        let vectors: Value =
            serde_json::from_str(include_str!("../tests/protocol_vectors.json")).unwrap();
        let rows = vectors["cases"][0]["files"]["process-1.jsonl"]
            .as_array()
            .unwrap();
        let mut data = Vec::new();
        for row in rows {
            serde_json::to_writer(&mut data, row).unwrap();
            data.push(b'\n');
        }
        let measurement = durante::collect::collect_bytes(&[(1, &data)], 1);
        let valid = json!({"schema":1,"authority":"diagnostic_only",
            "security":{"counter_integrity":"application_mutable"},
            "execution":{"state":"complete","cleanup_complete":true,"records_cleanup_complete":true},
            "measurement":measurement});
        assert!(valid_inner(&valid));
        let mut wrong = valid.clone();
        wrong["measurement"]["instructions"] = json!(u64::MAX);
        assert!(!valid_inner(&wrong));
        let mut partial = valid.clone();
        partial["execution"]["cleanup_complete"] = json!(false);
        assert!(!valid_inner(&partial));
        let mut unsupported = valid;
        unsupported["measurement"]["unsupported_rep_entries"] = json!(1);
        assert!(!valid_inner(&unsupported));
    }

    #[test]
    fn stdio_rejects_directory_socket_and_proc_capabilities() {
        let temporary = tempfile::tempdir().unwrap();
        let directory = File::open(temporary.path()).unwrap();
        assert!(verify_stdio_fd(directory.as_raw_fd()).is_err());
        let (socket, _peer) = std::os::unix::net::UnixStream::pair().unwrap();
        assert!(verify_stdio_fd(socket.as_raw_fd()).is_err());
        let proc = File::open("/proc/self/status").unwrap();
        assert!(verify_stdio_fd(proc.as_raw_fd()).is_err());
        let null = File::open("/dev/null").unwrap();
        assert!(verify_stdio_fd(null.as_raw_fd()).is_ok());
        let ordinary = File::create(temporary.path().join("ordinary")).unwrap();
        assert!(verify_stdio_fd(ordinary.as_raw_fd()).is_ok());
    }
}
