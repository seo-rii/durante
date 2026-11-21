use std::io::{self, Read};

fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    let mut fields = text.split_whitespace();
    let pairs: usize = fields.next().unwrap().parse().unwrap();
    let mut seed: u32 = fields.next().unwrap().parse().unwrap();
    let mut next = || {
        seed ^= seed << 13;
        seed ^= seed >> 17;
        seed ^= seed << 5;
        (seed & 0x7fff_ffff).max(1)
    };
    let mut sum = 0u64;
    for _ in 0..pairs {
        let (mut a, mut b) = (next(), next());
        while b != 0 {
            (a, b) = (b, a % b);
        }
        sum += u64::from(a);
    }
    println!("{sum}");
}
