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
    let mut bit = vec![0i64; n + 1];
    for i in 1..=n {
        bit[i] += sc.next_i64();
        let j = i + (i & i.wrapping_neg());
        if j <= n {
            bit[j] += bit[i];
        }
    }
    let mut out = Vec::with_capacity(1 << 22);
    for _ in 0..q {
        let (t, x, y) = (sc.next_usize(), sc.next_usize(), sc.next_i64());
        if t == 1 {
            let mut i = x;
            while i <= n {
                bit[i] += y;
                i += i & i.wrapping_neg();
            }
        } else {
            let mut s = 0i64;
            let mut i = y as usize;
            while i > 0 {
                s += bit[i];
                i -= i & i.wrapping_neg();
            }
            i = x - 1;
            while i > 0 {
                s -= bit[i];
                i -= i & i.wrapping_neg();
            }
            writeln!(out, "{}", s).unwrap();
        }
    }
    io::stdout().write_all(&out).unwrap();
}
