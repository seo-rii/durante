use std::io::{self, Read};

const MASK: u32 = (1 << 21) - 1;

// Addition, multiplication by an odd number, and xor with one's right shift
// each permute the 21-bit space. Every family/rank pair occurs exactly once.
fn permute(index: u32, seed: u32) -> u32 {
    let mut x = index.wrapping_add(seed) & MASK;
    x ^= x >> 10;
    x = x.wrapping_mul(0x45d9f3b) & MASK;
    x ^= x >> 8;
    x = x.wrapping_mul(0x119de1f3) & MASK;
    x ^ (x >> 11)
}

fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    let mut fields = text.split_whitespace();
    let count: u32 = fields.next().unwrap().parse().unwrap();
    let seed: u32 = fields.next().unwrap().parse().unwrap();
    assert_eq!(count, MASK + 1);

    let mut checksum = 0u64;
    for i in 0..count {
        let position = permute(i, seed);
        let rank = position >> 1;
        let scale = 1 + (rank.wrapping_mul(0x9e37_79b9) >> 22);
        let long = position & 1;
        let mut a = (34 + (514_229 - 34) * long) * scale;
        let mut b = (21 + (317_811 - 21) * long) * scale;
        while b != 0 {
            (a, b) = (b, a % b);
        }
        checksum += u64::from(a);
    }
    println!("{checksum}");
}
