use std::io::{self, Read};

fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    let mut fields = text.split_whitespace();
    let count: u32 = fields.next().unwrap().parse().unwrap();
    let shift: u32 = fields.next().unwrap().parse().unwrap();
    assert!(shift < 16 && count % (2 << shift) == 0);

    let low_mask = (1u32 << shift) - 1;
    let mut checksum = 0u64;
    for i in 0..count {
        // Drop the short/long selector bit. Each rank occurs once per family.
        let rank = ((i >> (shift + 1)) << shift) | (i & low_mask);
        let scale = 1 + (rank.wrapping_mul(0x9e37_79b9) >> 22);
        let long = (i >> shift) & 1;
        let mut a = (34 + (514_229 - 34) * long) * scale;
        let mut b = (21 + (317_811 - 21) * long) * scale;
        while b != 0 {
            (a, b) = (b, a % b);
        }
        checksum += u64::from(a);
    }
    println!("{checksum}");
}
