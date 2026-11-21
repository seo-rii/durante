use std::io::{self, Read};

fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    let mut fields = text.split_whitespace();
    let length: usize = fields.next().unwrap().parse().unwrap();
    let sweeps: usize = fields.next().unwrap().parse().unwrap();
    let mut seed: u32 = fields.next().unwrap().parse().unwrap();
    let values: Vec<u32> = (0..length)
        .map(|_| {
            seed ^= seed << 13;
            seed ^= seed >> 17;
            seed ^= seed << 5;
            seed & 0xffff
        })
        .collect();
    let mut checksum = 0u64;
    for sweep in 0..sweeps {
        let width = 257 + 2 * sweep;
        let mut sum: u64 = values[..width].iter().map(|&v| u64::from(v)).sum();
        checksum += sum;
        for i in width..length {
            sum -= u64::from(values[i - width]);
            sum += u64::from(values[i]);
            checksum += sum;
        }
    }
    println!("{checksum}");
}
