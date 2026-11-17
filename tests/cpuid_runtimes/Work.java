import java.security.MessageDigest;
import java.util.Arrays;
public class Work {
  public static void main(String[] a) throws Exception {
    int n = Integer.parseInt(a[0]);
    long[] v = new long[n]; long x = 42;
    for (int i = 0; i < n; i++) { x = x * 6364136223846793005L + 1442695040888963407L; v[i] = x >>> 11; }
    Arrays.sort(v);
    long s = 0; for (int i = 0; i < n; i++) s += v[i] * (i + 1);
    byte[] d = new byte[1 << 20]; for (int i = 0; i < d.length; i++) d[i] = (byte) i;
    byte[] h = MessageDigest.getInstance("SHA-256").digest(d);
    StringBuilder hex = new StringBuilder(); for (byte b : h) hex.append(String.format("%02x", b));
    System.out.println(s + " " + hex);
  }
}
