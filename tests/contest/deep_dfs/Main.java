import java.io.*;

public class Main {
    static final class Reader {
        private final java.io.DataInputStream in = new java.io.DataInputStream(new java.io.BufferedInputStream(System.in, 1 << 16));
        private final byte[] buf = new byte[1 << 16];
        private int len = 0, pos = 0;
        int read() throws java.io.IOException {
            if (pos == len) { len = in.read(buf, 0, buf.length); pos = 0; if (len <= 0) return -1; }
            return buf[pos++];
        }
        long nextLong() throws java.io.IOException {
            int c = read();
            while (c != 45 && (c < 48 || c > 57)) c = read();
            boolean neg = c == 45;
            if (neg) c = read();
            long x = 0;
            while (c >= 48 && c <= 57) { x = x * 10 + (c - 48); c = read(); }
            return neg ? -x : x;
        }
        int nextInt() throws java.io.IOException { return (int) nextLong(); }
        byte[] nextToken() throws java.io.IOException {
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            int c = read();
            while (c == 32 || c == 10 || c == 13) c = read();
            while (c > 32) { out.write(c); c = read(); }
            return out.toByteArray();
        }
    }

    static final long MOD = 1_000_000_007L;
    static int[] head, nxt, to;
    static long total = 0;
    static int maxDepth = 0;

    static void dfs(int v, int parent, int depth, long ancestorSum) {
        if (depth > maxDepth) maxDepth = depth;
        // Each ancestor contributes its label once for every node in its subtree.
        ancestorSum = (ancestorSum + v) % MOD;
        total = (total + ancestorSum) % MOD;
        for (int e = head[v]; e != -1; e = nxt[e]) {
            if (to[e] != parent) {
                // The input is a path rooted at one end, so it has one successor.
                dfs(to[e], v, depth + 1, ancestorSum);
                return;
            }
        }
    }

    public static void main(String[] args) throws Exception {
        // Classic contest workaround: run the recursion on a thread with a 512 MiB stack.
        Thread worker = new Thread(null, () -> {
            try {
                Reader r = new Reader();
                int n = r.nextInt();
                head = new int[n + 1];
                java.util.Arrays.fill(head, -1);
                nxt = new int[2 * (n - 1)];
                to = new int[2 * (n - 1)];
                for (int i = 0, e = 0; i < n - 1; i++) {
                    int u = r.nextInt(), v = r.nextInt();
                    to[e] = v; nxt[e] = head[u]; head[u] = e++;
                    to[e] = u; nxt[e] = head[v]; head[v] = e++;
                }
                dfs(1, 0, 0, 0);
                System.out.println(maxDepth + " " + total);
            } catch (IOException ex) {
                throw new UncheckedIOException(ex);
            }
        }, "solver", 1L << 29);
        worker.start();
        worker.join();
    }
}
