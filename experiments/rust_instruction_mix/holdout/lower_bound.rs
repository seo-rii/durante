use std::hint::black_box;
use std::io::{self, Read};

fn main() {
    let mut text = String::new();
    io::stdin().read_to_string(&mut text).unwrap();
    let mut fields = text.split_whitespace();
    let count: u32 = fields.next().unwrap().parse().unwrap();
    let queries: usize = fields.next().unwrap().parse().unwrap();
    let mut seed: u32 = fields.next().unwrap().parse().unwrap();
    let keys: Vec<u32> = (0..count).map(|i| 2 * i).collect();
    let sorted = black_box(keys.as_slice());
    let mut sum = 0u64;
    for _ in 0..queries {
        seed ^= seed << 13;
        seed ^= seed >> 17;
        seed ^= seed << 5;
        let target = seed % (2 * count + 1);
        sum += sorted.partition_point(|&value| value < target) as u64;
    }
    println!("{sum}");
}
