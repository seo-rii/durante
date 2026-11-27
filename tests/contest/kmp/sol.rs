use std::io::{self, Read, Write};

fn input() -> Vec<u8> {
    let mut buf = Vec::new();
    io::stdin().lock().read_to_end(&mut buf).unwrap();
    buf
}

fn main() {
    let data = input();
    let mut it = data.split(|b| b.is_ascii_whitespace()).filter(|t| !t.is_empty());
    let (t, p) = (it.next().unwrap(), it.next().unwrap());
    let m = p.len();
    let mut fail = vec![0usize; m];
    let mut k = 0;
    for i in 1..m {
        while k > 0 && p[i] != p[k] {
            k = fail[k - 1];
        }
        if p[i] == p[k] {
            k += 1;
        }
        fail[i] = k;
    }
    let (mut count, mut total, mut k) = (0u64, 0u64, 0usize);
    for (i, &ch) in t.iter().enumerate() {
        while k > 0 && ch != p[k] {
            k = fail[k - 1];
        }
        if ch == p[k] {
            k += 1;
        }
        if k == m {
            count += 1;
            total += (i + 2 - m) as u64;
            k = fail[k - 1];
        }
    }
    writeln!(io::stdout(), "{} {}", count, total).unwrap();
}
