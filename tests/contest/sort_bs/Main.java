import java.io.*;
import java.util.*;

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
        int[] a = new int[n];
        for (int i = 0; i < n; i++) a[i] = r.nextInt();
        Arrays.sort(a);
        StringBuilder out = new StringBuilder();
        for (int k = 0; k < q; k++) {
            int x = r.nextInt(), lo = 0, hi = n;
            while (lo < hi) { int mid = (lo + hi) >>> 1; if (a[mid] <= x) lo = mid + 1; else hi = mid; }
            out.append(lo).append('\n');
        }
        PrintWriter pw = new PrintWriter(new BufferedWriter(new OutputStreamWriter(System.out)));
        pw.print(out);
        pw.flush();
    }
}
