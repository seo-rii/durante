#![no_main]
use libfuzzer_sys::fuzz_target;

// First four bytes are a little-endian filename/root PID; all remaining bytes
// enter the production JSONL decoder unchanged, including invalid UTF-8.
fuzz_target!(|data: &[u8]| {
    let (pid, bytes) = if data.len() >= 4 {
        (
            u32::from_le_bytes(data[..4].try_into().unwrap()),
            &data[4..],
        )
    } else {
        (1, data)
    };
    let result = durante::collect::collect_bytes(&[(pid, bytes)], pid);
    durante_fuzz::assert_outcome(&result);
});
