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
        int n = r.nextInt(), m = r.nextInt();
        int[] eu = new int[m], ev = new int[m], ew = new int[m], start = new int[n + 2];
        for (int i = 0; i < m; i++) { eu[i] = r.nextInt(); ev[i] = r.nextInt(); ew[i] = r.nextInt(); start[eu[i] + 1]++; }
        for (int i = 1; i <= n + 1; i++) start[i] += start[i - 1];
        int[] to = new int[m], wt = new int[m], fill = start.clone();
        for (int i = 0; i < m; i++) { int p = fill[eu[i]]++; to[p] = ev[i]; wt[p] = ew[i]; }
        long[] dist = new long[n + 1];
        Arrays.fill(dist, Long.MAX_VALUE);
        dist[1] = 0;
        // Heap of (distance, vertex) packed in parallel arrays.
        long[] hd = new long[m + 1];
        int[] hv = new int[m + 1];
        int size = 0;
        hd[0] = 0; hv[0] = 1; size = 1;
        long reach = 0, sum = 0;
        while (size > 0) {
            long d = hd[0]; int u = hv[0];
            size--;
            if (size > 0) {
                long ld = hd[size]; int lv = hv[size]; int i = 0;
                while (true) {
                    int c = 2 * i + 1;
                    if (c >= size) break;
                    if (c + 1 < size && hd[c + 1] < hd[c]) c++;
                    if (hd[c] >= ld) break;
                    hd[i] = hd[c]; hv[i] = hv[c]; i = c;
                }
                hd[i] = ld; hv[i] = lv;
            }
            if (d != dist[u]) continue;
            reach++;
            sum += d;
            for (int p = start[u]; p < start[u + 1]; p++) {
                long nd = d + wt[p];
                int v = to[p];
                if (nd < dist[v]) {
                    dist[v] = nd;
                    int i = size++;
                    while (i > 0 && hd[(i - 1) / 2] > nd) { hd[i] = hd[(i - 1) / 2]; hv[i] = hv[(i - 1) / 2]; i = (i - 1) / 2; }
                    hd[i] = nd; hv[i] = v;
                }
            }
        }
        System.out.println((dist[n] == Long.MAX_VALUE ? -1 : dist[n]) + " " + reach + " " + sum);
    }
}
