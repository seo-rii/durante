"""Compare repeated cache probe counts independently of the time spent collecting them."""


def probe_repeatable(samples):
    """Return True or False for repeated measurements, or None when untested."""
    if len(samples) < 2:
        return None

    def counters(sample):
        return {key: value for key, value in sample.items() if key not in ("probe_cpu_s", "probe_wall_s")}

    first = counters(samples[0])
    return all(counters(sample) == first for sample in samples[1:]) if first else None
