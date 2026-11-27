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

    public static void main(String[] args) throws IOException {
        Reader r = new Reader();
        int h = r.nextInt(), w = r.nextInt();
        byte[] g = new byte[h * w];
        for (int i = 0; i < h; i++) System.arraycopy(r.nextToken(), 0, g, i * w, w);
        int[] dist = new int[h * w];
        java.util.Arrays.fill(dist, -1);
        int[] q = new int[h * w];
        int head = 0, tail = 0;
        if (g[0] == '.') { dist[0] = 0; q[tail++] = 0; }
        while (head < tail) {
            int cur = q[head++], row = cur / w, col = cur % w;
            int[] cand = {row > 0 ? cur - w : -1, row + 1 < h ? cur + w : -1, col > 0 ? cur - 1 : -1, col + 1 < w ? cur + 1 : -1};
            for (int nb : cand) {
                if (nb < 0 || g[nb] != '.' || dist[nb] != -1) continue;
                dist[nb] = dist[cur] + 1;
                q[tail++] = nb;
            }
        }
        System.out.println(dist[h * w - 1] + " " + tail);
    }
}
