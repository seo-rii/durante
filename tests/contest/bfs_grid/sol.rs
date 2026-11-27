use std::io::{self, Read, Write};

fn input() -> Vec<u8> {
    let mut buf = Vec::new();
    io::stdin().lock().read_to_end(&mut buf).unwrap();
    buf
}

fn main() {
    let data = input();
    let mut it = data.split(|b| b.is_ascii_whitespace()).filter(|t| !t.is_empty());
    let h: usize = std::str::from_utf8(it.next().unwrap()).unwrap().parse().unwrap();
    let w: usize = std::str::from_utf8(it.next().unwrap()).unwrap().parse().unwrap();
    let mut g = Vec::with_capacity(h * w);
    for _ in 0..h {
        g.extend_from_slice(it.next().unwrap());
    }
    let mut dist = vec![-1i32; h * w];
    let mut q = Vec::with_capacity(h * w);
    if g[0] == b'.' {
        dist[0] = 0;
        q.push(0usize);
    }
    let mut head = 0;
    while head < q.len() {
        let cur = q[head];
        head += 1;
        let (r, c) = (cur / w, cur % w);
        let d = dist[cur] + 1;
        let cands = [(r > 0, cur.wrapping_sub(w)), (r + 1 < h, cur + w), (c > 0, cur.wrapping_sub(1)), (c + 1 < w, cur + 1)];
        for &(ok, nb) in cands.iter() {
            if ok && g[nb] == b'.' && dist[nb] < 0 {
                dist[nb] = d;
                q.push(nb);
            }
        }
    }
    writeln!(io::stdout(), "{} {}", dist[h * w - 1], q.len()).unwrap();
}
