use std::io::{self, Read, Write};

fn input() -> Vec<u8> {
    let mut buf = Vec::new();
    io::stdin().lock().read_to_end(&mut buf).unwrap();
    buf
}

fn main() {
    let data = input();
    let mut it = data.split(|b| b.is_ascii_whitespace()).filter(|t| !t.is_empty());
    let (a, b) = (it.next().unwrap(), it.next().unwrap());
    let m = b.len();
    let mut prev = vec![0u32; m + 1];
    let mut cur = vec![0u32; m + 1];
    for &ca in a {
        cur[0] = 0;
        for j in 1..=m {
            cur[j] = if ca == b[j - 1] { prev[j - 1] + 1 } else { prev[j].max(cur[j - 1]) };
        }
        std::mem::swap(&mut prev, &mut cur);
    }
    writeln!(io::stdout(), "{}", prev[m]).unwrap();
}
