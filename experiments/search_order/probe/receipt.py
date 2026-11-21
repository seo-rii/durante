"""Validate a schema-1 single-process terminal-index measurement."""

PERIOD = 1021


def validate(data, pid):
    fields = {"schema", "pid", "threads", "pc", "visits", "pairs", "suffix_sum"}
    if type(data) is not dict or set(data) != fields | {"suffix_histogram"}:
        raise ValueError("incorrect terminal-index receipt fields")
    if any(type(v) is not int or not 0 <= v < 2**64 for v in (data[k] for k in fields)):
        raise ValueError("invalid unsigned terminal-index counter")
    if data["schema"] != 1 or data["threads"] != 1 or data["pid"] != pid or pid <= 0:
        raise ValueError("wrong terminal-index process or schema")
    rounds, extra = divmod(data["visits"], PERIOD)
    if data["pairs"] != rounds + (extra >= 2) or data["suffix_sum"] > 60 * data["pairs"]:
        raise ValueError("terminal-index pair schedule or suffix bound disagrees")
    hist = data["suffix_histogram"]
    if type(hist) is not list or len(hist) != 61 or any(type(n) is not int or not 0 <= n < 2**64 for n in hist):
        raise ValueError("invalid terminal-index histogram")
    if sum(hist) != data["pairs"] or sum(i * n for i, n in enumerate(hist)) != data["suffix_sum"]:
        raise ValueError("inconsistent terminal-index histogram")
    return data
