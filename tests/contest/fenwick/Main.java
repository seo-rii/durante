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
        int n = r.nextInt(), q = r.nextInt();
        long[] bit = new long[n + 1];
        for (int i = 1; i <= n; i++) {
            bit[i] += r.nextLong();
            int j = i + (i & -i);
            if (j <= n) bit[j] += bit[i];
        }
        StringBuilder out = new StringBuilder();
        for (int k = 0; k < q; k++) {
            int t = r.nextInt(), x = r.nextInt();
            long y = r.nextLong();
            if (t == 1) {
                for (int i = x; i <= n; i += i & -i) bit[i] += y;
            } else {
                long s = 0;
                for (int i = (int) y; i > 0; i -= i & -i) s += bit[i];
                for (int i = x - 1; i > 0; i -= i & -i) s -= bit[i];
                out.append(s).append('\n');
            }
        }
        PrintWriter pw = new PrintWriter(new BufferedWriter(new OutputStreamWriter(System.out)));
        pw.print(out);
        pw.flush();
    }
}
