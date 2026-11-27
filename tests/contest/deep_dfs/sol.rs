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

const MOD: u64 = 1_000_000_007;

struct Tree { head: Vec<i32>, nxt: Vec<i32>, to: Vec<i32>, total: u64, max_depth: usize }

fn dfs(t: &mut Tree, v: usize, parent: usize, depth: usize) -> u64 {
    if depth > t.max_depth {
        t.max_depth = depth;
    }
    let mut size = 1u64;
    let mut e = t.head[v];
    while e != -1 {
        let u = t.to[e as usize] as usize;
        if u != parent {
            size += dfs(t, u, v, depth + 1);
        }
        e = t.nxt[e as usize];
    }
    t.total = (t.total + size % MOD * v as u64) % MOD;
    size
}

fn main() {
    // Classic contest workaround: recurse on a thread with a 512 MiB stack.
    std::thread::Builder::new().stack_size(512 << 20).spawn(|| {
        let mut sc = Scanner { data: input(), pos: 0 };
        let n = sc.next_usize();
        let mut t = Tree { head: vec![-1; n + 1], nxt: vec![0; 2 * (n - 1)], to: vec![0; 2 * (n - 1)], total: 0, max_depth: 0 };
        let mut e = 0;
        for _ in 0..n - 1 {
            let (u, v) = (sc.next_usize(), sc.next_usize());
            t.to[e] = v as i32; t.nxt[e] = t.head[u]; t.head[u] = e as i32; e += 1;
            t.to[e] = u as i32; t.nxt[e] = t.head[v]; t.head[v] = e as i32; e += 1;
        }
        dfs(&mut t, 1, 0, 0);
        writeln!(io::stdout(), "{} {}", t.max_depth, t.total).unwrap();
    }).unwrap().join().unwrap();
}
