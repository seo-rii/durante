//! Bounded pipes for an isolated child; only pipe descriptors reach the target.
use serde_json::{json, Value};
use std::fs::{self, DirBuilder, File, OpenOptions};
use std::io::{self, Read, Seek, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{DirBuilderExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};

pub(crate) const DEFAULT_LIMIT: u64 = 1 << 20;
pub(crate) const MAX_LIMIT: u64 = 64 << 20;
const READ_BYTES: usize = 8192;
const READS_PER_STREAM: usize = 4;

struct Stream {
    destination: File,
    pipe: Option<File>,
    bytes: Vec<u8>,
    observed: u64,
    eof: bool,
}

pub(crate) struct Capture {
    path: PathBuf,
    directory: File,
    streams: [Stream; 2],
    limit: u64,
    truncated: bool,
    published: bool,
}

fn private_directory(path: &Path) -> io::Result<File> {
    DirBuilder::new().mode(0o700).create(path)?;
    OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
}

fn destination(directory: &File, name: &std::ffi::CStr) -> io::Result<File> {
    // SAFETY: a static leaf and held directory FD; no following or replacement.
    let fd = unsafe {
        libc::openat(
            directory.as_raw_fd(),
            name.as_ptr(),
            libc::O_WRONLY
                | libc::O_CREAT
                | libc::O_EXCL
                | libc::O_NOFOLLOW
                | libc::O_CLOEXEC
                | libc::O_NONBLOCK,
            0o600,
        )
    };
    if fd < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful openat returns a new owned descriptor.
    Ok(unsafe { File::from_raw_fd(fd) })
}

fn pipe() -> io::Result<(File, File)> {
    let mut descriptors = [-1; 2];
    // SAFETY: pipe2 initializes both descriptors on success.
    if unsafe { libc::pipe2(descriptors.as_mut_ptr(), libc::O_CLOEXEC) } < 0 {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: successful pipe2 returns two new owned descriptors.
    let read = unsafe { File::from_raw_fd(descriptors[0]) };
    let write = unsafe { File::from_raw_fd(descriptors[1]) };
    // Only the reader is nonblocking. Changing the writer's open-file flags
    // would change the target's ordinary write semantics.
    if unsafe { libc::fcntl(read.as_raw_fd(), libc::F_SETFL, libc::O_NONBLOCK) } < 0 {
        return Err(io::Error::last_os_error());
    }
    Ok((read, write))
}

impl Capture {
    pub(crate) fn create(path: &Path, limit: u64) -> io::Result<Self> {
        if !(1..=MAX_LIMIT).contains(&limit) {
            return Err(io::Error::other("output limit outside bound"));
        }
        let directory = private_directory(path)?;
        let stream = |name: &std::ffi::CStr| -> io::Result<Stream> {
            let mut bytes = Vec::new();
            bytes
                .try_reserve_exact(limit as usize)
                .map_err(|_| io::Error::other("cannot reserve bounded output buffer"))?;
            Ok(Stream {
                destination: destination(&directory, name)?,
                pipe: None,
                bytes,
                observed: 0,
                eof: false,
            })
        };
        let streams = [stream(c"stdout")?, stream(c"stderr")?];
        let capture = Self {
            path: path.to_path_buf(),
            directory,
            streams,
            limit,
            truncated: false,
            published: false,
        };
        capture.verify(0, 0)?;
        Ok(capture)
    }

    pub(crate) fn configure(&mut self, command: &mut Command) -> io::Result<()> {
        let (stdout, stdout_write) = pipe()?;
        let (stderr, stderr_write) = pipe()?;
        self.streams[0].pipe = Some(stdout);
        self.streams[1].pipe = Some(stderr);
        command
            .stdout(Stdio::from(stdout_write))
            .stderr(Stdio::from(stderr_write));
        Ok(())
    }

    /// At most 64 KiB per tick. The owner checks cancellation/deadline each tick.
    /// Returns true on the first observed byte beyond the combined limit.
    pub(crate) fn drain_tick(&mut self) -> io::Result<bool> {
        if self.truncated {
            return Ok(true);
        }
        let mut remaining = self.limit
            - self
                .streams
                .iter()
                .map(|s| s.bytes.len() as u64)
                .sum::<u64>();
        let mut buffer = [0_u8; READ_BYTES];
        // Alternate streams so a producer on one cannot starve the other.
        for _ in 0..READS_PER_STREAM {
            for stream in &mut self.streams {
                let Some(pipe) = stream.pipe.as_mut() else {
                    continue;
                };
                match pipe.read(&mut buffer) {
                    Ok(0) => {
                        stream.eof = true;
                        stream.pipe = None;
                    }
                    Ok(count) => {
                        stream.observed += count as u64;
                        let keep = count.min(remaining as usize);
                        stream.bytes.extend_from_slice(&buffer[..keep]);
                        remaining -= keep as u64;
                        if keep != count {
                            self.truncated = true;
                            return Ok(true);
                        }
                    }
                    Err(error)
                        if matches!(
                            error.kind(),
                            io::ErrorKind::WouldBlock | io::ErrorKind::Interrupted
                        ) => {}
                    Err(error) => return Err(error),
                }
            }
        }
        Ok(false)
    }

    pub(crate) fn eof(&self) -> bool {
        self.streams.iter().all(|stream| stream.eof)
    }

    pub(crate) fn close_pipes(&mut self) {
        for stream in &mut self.streams {
            stream.pipe = None;
        }
    }

    fn verify(&self, stdout_length: u64, stderr_length: u64) -> io::Result<()> {
        let held = self.directory.metadata()?;
        let named = fs::symlink_metadata(&self.path)?;
        if !named.is_dir()
            || named.dev() != held.dev()
            || named.ino() != held.ino()
            || named.uid() != held.uid()
            || held.uid() != unsafe { libc::geteuid() }
            || named.permissions().mode() & 0o077 != 0
        {
            return Err(io::Error::other("output directory changed"));
        }
        for ((stream, name), length) in self
            .streams
            .iter()
            .zip([c"stdout", c"stderr"])
            .zip([stdout_length, stderr_length])
        {
            let held = stream.destination.metadata()?;
            let mut named = std::mem::MaybeUninit::<libc::stat>::uninit();
            // SAFETY: held directory, static single leaf and initialized-on-success stat.
            if unsafe {
                libc::fstatat(
                    self.directory.as_raw_fd(),
                    name.as_ptr(),
                    named.as_mut_ptr(),
                    libc::AT_SYMLINK_NOFOLLOW,
                )
            } != 0
            {
                return Err(io::Error::last_os_error());
            }
            let named = unsafe { named.assume_init() };
            if !held.is_file()
                || named.st_mode & libc::S_IFMT != libc::S_IFREG
                || named.st_dev != held.dev()
                || named.st_ino != held.ino()
                || named.st_nlink != 1
                || held.nlink() != 1
                || named.st_size < 0
                || named.st_size as u64 != length
                || held.len() != length
                || named.st_mode & 0o077 != 0
            {
                return Err(io::Error::other("output file changed"));
            }
        }
        Ok(())
    }

    /// Called only after owned children are reaped. Storage latency cannot stall
    /// the active guest's watchdog. Failed publication leaves inspectable files.
    pub(crate) fn publish(&mut self) -> io::Result<()> {
        self.verify(0, 0)?;
        for stream in &mut self.streams {
            stream.destination.write_all(&stream.bytes)?;
            stream.destination.sync_data()?;
            if stream.destination.stream_position()? != stream.bytes.len() as u64 {
                return Err(io::Error::other("output file length mismatch"));
            }
        }
        self.verify(
            self.streams[0].bytes.len() as u64,
            self.streams[1].bytes.len() as u64,
        )?;
        self.published = true;
        Ok(())
    }

    pub(crate) fn summary(&self) -> Value {
        json!({"mode":"captured", "directory":self.path, "limit_bytes":self.limit,
            "stdout_captured_bytes":self.streams[0].bytes.len(),
            "stderr_captured_bytes":self.streams[1].bytes.len(),
            "observed_bytes":self.streams.iter().map(|s| s.observed).sum::<u64>(),
            "truncated":self.truncated, "published":self.published,
            "complete":self.eof() && !self.truncated && self.published})
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::os::unix::fs::symlink;
    use std::time::{Duration, Instant};

    fn sample(limit: u64, stdout: &[u8], stderr: &[u8]) -> (tempfile::TempDir, Capture) {
        let directory = tempfile::tempdir().unwrap();
        let mut capture = Capture::create(&directory.path().join("capture"), limit).unwrap();
        for (stream, data) in capture.streams.iter_mut().zip([stdout, stderr]) {
            let (read, mut write) = pipe().unwrap();
            stream.pipe = Some(read);
            write.write_all(data).unwrap();
        }
        (directory, capture)
    }

    #[test]
    fn exact_combined_limit_preserves_binary_output_and_eof() {
        let (directory, mut capture) = sample(9, b"one\0", b"two\n\xff");
        assert!(!capture.drain_tick().unwrap());
        assert!(capture.eof());
        capture.publish().unwrap();
        assert_eq!(
            fs::read(directory.path().join("capture/stdout")).unwrap(),
            b"one\0"
        );
        assert_eq!(
            fs::read(directory.path().join("capture/stderr")).unwrap(),
            b"two\n\xff"
        );
        assert_eq!(capture.summary()["observed_bytes"], 9);
        assert_eq!(capture.summary()["complete"], true);
        assert_eq!(
            fs::metadata(directory.path().join("capture"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o700
        );
        assert_eq!(
            fs::metadata(directory.path().join("capture/stdout"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
    }

    #[test]
    fn first_extra_byte_truncates_once_at_the_combined_limit() {
        let (directory, mut capture) = sample(8, b"1234", b"56789");
        assert!(capture.drain_tick().unwrap());
        let observed = capture.summary()["observed_bytes"].clone();
        assert!(capture.drain_tick().unwrap());
        assert_eq!(capture.summary()["observed_bytes"], observed);
        capture.close_pipes();
        capture.publish().unwrap();
        assert_eq!(
            fs::read(directory.path().join("capture/stdout")).unwrap(),
            b"1234"
        );
        assert_eq!(
            fs::read(directory.path().join("capture/stderr")).unwrap(),
            b"5678"
        );
        assert_eq!(capture.summary()["truncated"], true);
        assert_eq!(capture.summary()["complete"], false);
    }

    #[test]
    fn empty_live_pipes_never_block_a_deadline_tick() {
        let (_directory, mut capture) = sample(128, b"", b"");
        let (read, _writer) = pipe().unwrap();
        capture.streams[0].pipe = Some(read);
        let start = Instant::now();
        for _ in 0..100 {
            assert!(!capture.drain_tick().unwrap());
        }
        assert!(!capture.eof());
        assert!(start.elapsed() < Duration::from_secs(1));
    }

    #[test]
    fn flood_from_a_real_child_is_bounded_and_reaped() {
        let directory = tempfile::tempdir().unwrap();
        let mut capture = Capture::create(&directory.path().join("capture"), 8192).unwrap();
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "i=0; while [ \"$i\" -lt 10000 ]; do printf 1234567890123456; printf abcdefghijklmnop >&2; i=$((i+1)); done"]);
        capture.configure(&mut command).unwrap();
        let mut child = command.spawn().unwrap();
        drop(command);
        let deadline = Instant::now() + Duration::from_secs(3);
        let limited = loop {
            if capture.drain_tick().unwrap() {
                break true;
            }
            if capture.eof() || Instant::now() >= deadline {
                break false;
            }
            std::thread::sleep(Duration::from_millis(1));
        };
        let _ = child.kill();
        child.wait().unwrap();
        capture.close_pipes();
        assert!(limited);
        capture.publish().unwrap();
        let sizes: u64 = ["stdout", "stderr"]
            .iter()
            .map(|name| {
                fs::metadata(directory.path().join("capture").join(name))
                    .unwrap()
                    .len()
            })
            .sum();
        assert_eq!(sizes, 8192);
        assert!(capture.summary()["observed_bytes"].as_u64().unwrap() <= 8192 + READ_BYTES as u64);
    }

    #[test]
    fn collisions_replacement_links_and_growth_never_overwrite_other_files() {
        let directory = tempfile::tempdir().unwrap();
        let existing = directory.path().join("existing");
        fs::create_dir(&existing).unwrap();
        assert!(Capture::create(&existing, 10).is_err());
        symlink(&existing, directory.path().join("alias")).unwrap();
        assert!(Capture::create(&directory.path().join("alias"), 10).is_err());
        for mode in ["symlink", "hardlink", "growth", "directory"] {
            let (directory, mut capture) = sample(10, b"a", b"b");
            capture.drain_tick().unwrap();
            let victim = directory.path().join("victim");
            fs::write(&victim, b"untouched").unwrap();
            let output = directory.path().join("capture/stdout");
            match mode {
                "symlink" => {
                    fs::remove_file(&output).unwrap();
                    symlink(&victim, &output).unwrap();
                }
                "hardlink" => {
                    fs::hard_link(&output, directory.path().join("extra-link")).unwrap();
                }
                "growth" => {
                    fs::write(&output, b"growth").unwrap();
                }
                "directory" => {
                    fs::rename(
                        directory.path().join("capture"),
                        directory.path().join("original"),
                    )
                    .unwrap();
                    fs::create_dir(directory.path().join("capture")).unwrap();
                }
                _ => unreachable!(),
            }
            assert!(capture.publish().is_err(), "{mode}");
            assert_eq!(fs::read(victim).unwrap(), b"untouched");
            assert_eq!(capture.summary()["published"], false);
        }
        for limit in [0, MAX_LIMIT + 1] {
            let path = directory.path().join("invalid");
            assert!(Capture::create(&path, limit).is_err());
            assert!(!path.exists());
        }
    }
}
