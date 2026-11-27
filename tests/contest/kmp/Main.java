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
        byte[] t = r.nextToken(), p = r.nextToken();
        int m = p.length;
        int[] fail = new int[m];
        for (int i = 1, k = 0; i < m; i++) {
            while (k > 0 && p[i] != p[k]) k = fail[k - 1];
            if (p[i] == p[k]) k++;
            fail[i] = k;
        }
        long count = 0, total = 0;
        for (int i = 0, k = 0; i < t.length; i++) {
            while (k > 0 && t[i] != p[k]) k = fail[k - 1];
            if (t[i] == p[k]) k++;
            if (k == m) { count++; total += i - m + 2; k = fail[k - 1]; }
        }
        System.out.println(count + " " + total);
    }
}
