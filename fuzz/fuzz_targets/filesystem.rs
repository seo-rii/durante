#![no_main]
use libfuzzer_sys::fuzz_target;
use std::ffi::OsString;
use std::fs::{self, File};
use std::os::unix::ffi::{OsStrExt, OsStringExt};
use std::os::unix::fs::symlink;
use std::path::PathBuf;

struct Fixture(PathBuf);
impl Fixture {
    fn new() -> Self {
        let mut template = std::env::temp_dir()
            .join("durante-fs-fuzz-XXXXXX")
            .into_os_string()
            .into_vec();
        template.push(0);
        // SAFETY: writable, NUL-terminated mkdtemp template, owned for this call.
        // A fresh 0700 directory pins every path created by this target.
        let result = unsafe { libc::mkdtemp(template.as_mut_ptr().cast()) };
        assert!(!result.is_null(), "cannot create filesystem fuzz fixture");
        template.pop();
        Self(PathBuf::from(OsString::from_vec(template)))
    }
}
impl Drop for Fixture {
    fn drop(&mut self) {
        // The target creates at most four leaves and one directory, with no
        // concurrent writer. Symlinks only point inside this owned fixture.
        fs::remove_dir_all(&self.0).expect("cannot remove filesystem fuzz fixture");
    }
}

fuzz_target!(|data: &[u8]| {
    let fixture = Fixture::new();
    let records = fixture.0.join("records");
    fs::create_dir(&records).unwrap();
    let victim = fixture.0.join("sentinel");
    let seed = include_bytes!("../corpus/raw_stream/single");
    // This committed seed has the raw-stream target's four-byte PID prefix.
    let valid = &seed[4..];
    // A valid sentinel makes an accidental symlink/hardlink follow observable:
    // parsing arbitrary invalid sentinel text could mask that filesystem bug.
    fs::write(&victim, valid).unwrap();
    let leaf = records.join("process-1.jsonl");
    let mode = data.first().copied().unwrap_or(0) % 10;
    let mut force_invalid = false;
    let mut root = records.clone();
    match mode {
        0 => fs::write(&leaf, valid).unwrap(),
        1 => fs::write(&leaf, &data[1..]).unwrap(),
        2 => {
            let path = std::ffi::CString::new(leaf.as_os_str().as_bytes()).unwrap();
            // SAFETY: valid terminated leaf inside the owned fixture; no reader
            // is ever opened by the target. Production must not block on it.
            assert_eq!(unsafe { libc::mkfifo(path.as_ptr(), 0o600) }, 0);
            force_invalid = true;
        }
        3 => {
            symlink(&victim, &leaf).unwrap();
            force_invalid = true;
        }
        4 => {
            fs::hard_link(&victim, &leaf).unwrap();
            force_invalid = true;
        }
        5 => {
            fs::create_dir(&leaf).unwrap();
            force_invalid = true;
        }
        6 => {
            File::create(&leaf)
                .unwrap()
                .set_len((durante::collect::MAX_FILE_BYTES + 1) as u64)
                .unwrap();
            force_invalid = true;
        }
        7 => {
            fs::write(records.join("process-01.jsonl"), valid).unwrap();
            force_invalid = true;
        }
        8 => {
            fs::write(&leaf, valid).unwrap();
            root = fixture.0.join("root-link");
            symlink(&records, &root).unwrap();
            force_invalid = true;
        }
        _ => {
            root = victim.clone();
            force_invalid = true;
        }
    }
    let result = durante::collect::collect(&root, 1);
    durante_fuzz::assert_outcome(&result);
    if force_invalid {
        assert_eq!(result["valid"], false);
    }
    if mode == 0 {
        assert_eq!(result["valid"], true);
    }
    assert_eq!(fs::read(&victim).unwrap(), valid);
});
