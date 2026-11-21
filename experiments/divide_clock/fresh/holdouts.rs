// Two independent programs selected at compile time. Build with one --cfg selector.
#[cfg(floor_groups)]
fn main() {
    let mut state = 0x93a2_bcb1u32;
    let mut sum = 0u64;
    for _ in 0..256 {
        state = state.wrapping_mul(1664525).wrapping_add(1013904223);
        let n = (1 << 27) + (state & ((1 << 27) - 1));
        let mut first = 1;
        while first <= n {
            let quotient = n / first;
            let last = n / quotient;
            sum += u64::from(last - first + 1) * u64::from(quotient);
            first = last + 1;
        }
    }
    println!("{sum}");
}

#[cfg(batch_quotients)]
fn main() {
    let mut state = 0x73a9_1d2fu32;
    let mut pairs = Vec::with_capacity(4096);
    for _ in 0..4096 {
        state = state.wrapping_mul(1664525).wrapping_add(1013904223);
        let dividend = state;
        state = state.wrapping_mul(1664525).wrapping_add(1013904223);
        pairs.push((dividend, (state & 0xffff) + 1));
    }
    let mut total = 0u64;
    for _ in 0..8192 {
        // Make the read-only slice opaque per pass to preserve all divisions.
        for &(n, d) in std::hint::black_box(&pairs[..]) {
            total += u64::from(n / d);
        }
    }
    println!("{total}");
}
