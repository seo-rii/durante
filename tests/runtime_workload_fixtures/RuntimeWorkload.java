import java.lang.management.ManagementFactory;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

public class RuntimeWorkload {
    private static long collections() {
        return ManagementFactory.getGarbageCollectorMXBeans().stream()
            .mapToLong(bean -> Math.max(0, bean.getCollectionCount())).sum();
    }

    private static void threads() throws Exception {
        CountDownLatch ready = new CountDownLatch(2);
        CountDownLatch start = new CountDownLatch(1);
        AtomicReference<Throwable> failure = new AtomicReference<>();
        long[] values = new long[2];
        Thread[] workers = new Thread[2];
        for (int index = 0; index < workers.length; ++index) {
            final int worker = index;
            workers[index] = new Thread(() -> {
                ready.countDown();
                try {
                    start.await();
                    long value = 0;
                    for (int item = 1; item <= 10000; ++item) value += item * (worker + 1L);
                    values[worker] = value;
                } catch (Throwable error) {
                    failure.compareAndSet(null, error);
                }
            });
            workers[index].start();
        }
        if (!ready.await(10, TimeUnit.SECONDS)) throw new AssertionError("workers did not start");
        start.countDown();
        for (Thread worker : workers) {
            worker.join(10000);
            if (worker.isAlive()) throw new AssertionError("worker did not finish");
        }
        if (failure.get() != null) throw new AssertionError(failure.get());
        System.out.println("threads:" + (values[0] + values[1]));
    }

    private static void allocationGc() {
        long before = collections();
        long checksum = 0;
        byte[][] live = new byte[128][];
        for (int round = 0; round < 16; ++round) {
            for (int item = 0; item < live.length; ++item) {
                byte[] block = new byte[1024];
                int value = (round + item) & 255;
                block[0] = (byte)value;
                block[block.length - 1] = (byte)(255 - value);
                live[item] = block;
                checksum += Byte.toUnsignedInt(block[0]) + Byte.toUnsignedInt(block[1023]);
            }
            if (round % 4 == 3) System.gc();
        }
        if (collections() <= before) throw new AssertionError("no garbage collection observed");
        long retained = 0;
        for (int item = 0; item < live.length; ++item) {
            int expected = (15 + item) & 255;
            if (live[item].length != 1024 || Byte.toUnsignedInt(live[item][0]) != expected
                    || Byte.toUnsignedInt(live[item][1023]) != 255 - expected)
                throw new AssertionError("retained allocation changed after collection");
            retained += Byte.toUnsignedInt(live[item][0]) + Byte.toUnsignedInt(live[item][1023]);
        }
        System.out.println("allocation-gc:" + checksum + ":retained:" + retained + ":observed");
    }

    private static void processes(String executable) throws Exception {
        long total = 0;
        for (int seed : new int[] {7, 11}) {
            List<String> command = new ArrayList<>(List.of(executable, "-Xmx64m", "-Xss256k",
                "-XX:CompressedClassSpaceSize=32m", "-XX:ReservedCodeCacheSize=32m",
                "-XX:ActiveProcessorCount=2", "-XX:+UseSerialGC", "-cp",
                System.getProperty("java.class.path"), "RuntimeWorkload", "child", Integer.toString(seed)));
            Process child = new ProcessBuilder(command).redirectError(ProcessBuilder.Redirect.INHERIT).start();
            if (!child.waitFor(45, TimeUnit.SECONDS)) {
                child.destroyForcibly().waitFor();
                throw new AssertionError("child deadline");
            }
            String text = new String(child.getInputStream().readNBytes(128), StandardCharsets.UTF_8);
            int expected = seed * seed + 17;
            if (child.exitValue() != 0 || !text.equals("child:" + expected + "\n"))
                throw new AssertionError("child output or exit mismatch");
            total += expected;
        }
        System.out.println("processes:" + total);
    }

    public static void main(String[] args) throws Exception {
        switch (args[0]) {
            case "threads": threads(); break;
            case "allocation-gc": allocationGc(); break;
            case "processes": processes(args[1]); break;
            case "child": System.out.println("child:" + (Integer.parseInt(args[1]) * Integer.parseInt(args[1]) + 17)); break;
            default: throw new IllegalArgumentException("unknown fixture");
        }
    }
}
