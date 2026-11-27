use std::io::{self, Read, Write};

fn input() -> Vec<u8> {
    let mut buf = Vec::new();
    io::stdin().lock().read_to_end(&mut buf).unwrap();
    buf
}

struct Scanner { data: Vec<u8>, pos: usize }
impl Scanner {
    fn next_i64(&mut self) -> i64 {
        let d = &self.data;
        while d[self.pos] != b'-' && !d[self.pos].is_ascii_digit() { self.pos += 1; }
        let neg = d[self.pos] == b'-';
        if neg { self.pos += 1; }
        let mut x = 0i64;
        while self.pos < d.len() && d[self.pos].is_ascii_digit() { x = x * 10 + (d[self.pos] - b'0') as i64; self.pos += 1; }
        if neg { -x } else { x }
    }
    fn next_usize(&mut self) -> usize { self.next_i64() as usize }
}

fn main() {
    let mut sc = Scanner { data: input(), pos: 0 };
    let (n, q) = (sc.next_usize(), sc.next_usize());
    let mut a: Vec<i64> = (0..n).map(|_| sc.next_i64()).collect();
    a.sort_unstable();
    let mut out = Vec::with_capacity(q * 8);
    for _ in 0..q {
        let x = sc.next_i64();
        writeln!(out, "{}", a.partition_point(|&v| v <= x)).unwrap();
    }
    io::stdout().write_all(&out).unwrap();
}
