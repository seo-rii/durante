public class RuntimeProbe {
    private static long sum() {
        long value = 0;
        for (int index = 0; index < 50000; ++index) value += index;
        return value;
    }
    public static void main(String[] args) {
        long value = 0;
        for (int iteration = 0; iteration < 50; ++iteration) value = sum();
        System.out.println(value);
    }
}
