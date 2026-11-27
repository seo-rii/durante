use std::cmp::Reverse;
use std::collections::BinaryHeap;
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
    let (n, m) = (sc.next_usize(), sc.next_usize());
    let mut edges = Vec::with_capacity(m);
    let mut start = vec![0usize; n + 2];
    for _ in 0..m {
        let (u, v, w) = (sc.next_usize(), sc.next_usize(), sc.next_i64());
        start[u + 1] += 1;
        edges.push((u, v, w));
    }
    for i in 1..=n + 1 {
        start[i] += start[i - 1];
    }
    let mut fill = start.clone();
    let mut adj = vec![(0usize, 0i64); m];
    for &(u, v, w) in &edges {
        adj[fill[u]] = (v, w);
        fill[u] += 1;
    }
    let mut dist = vec![i64::MAX; n + 1];
    dist[1] = 0;
    let mut pq = BinaryHeap::new();
    pq.push(Reverse((0i64, 1usize)));
    let (mut reach, mut sum) = (0i64, 0i64);
    while let Some(Reverse((d, u))) = pq.pop() {
        if d != dist[u] {
            continue;
        }
        reach += 1;
        sum += d;
        for &(v, w) in &adj[start[u]..start[u + 1]] {
            let nd = d + w;
            if nd < dist[v] {
                dist[v] = nd;
                pq.push(Reverse((nd, v)));
            }
        }
    }
    let dn = if dist[n] == i64::MAX { -1 } else { dist[n] };
    writeln!(io::stdout(), "{} {} {}", dn, reach, sum).unwrap();
}
