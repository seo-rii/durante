"""Validate an opt-in loop-trip receipt for a single application process."""


def validate(data, pid):
    def require(value):
        if not value:
            raise ValueError("invalid trip probe receipt")

    require(type(data) is dict and set(data) == {"schema", "pid", "threads", "sites"})
    require(all(type(data[k]) is int for k in ("schema", "pid", "threads")))
    require(data["schema"] == data["threads"] == 1 and data["pid"] == pid and pid > 0)
    require(type(data["sites"]) is list and len(data["sites"]) <= 1024)
    fields = {"branch_offset", "target_offset", "visits", "completed", "changes",
              "last_length", "pending_length"}
    seen = set()
    for site in data["sites"]:
        require(type(site) is dict and set(site) == fields)
        require(all(type(v) is int and 0 <= v < 2**64 for v in site.values()))
        branch = site["branch_offset"]
        require(branch not in seen and 0 < branch - site["target_offset"] <= 256)
        seen.add(branch)
        visits, count = site["visits"], site["completed"]
        changes, last, pending = site["changes"], site["last_length"], site["pending_length"]
        require(visits > 0 and pending <= visits and changes <= max(0, count - 1))
        if count:
            require(last > 0 and visits - pending >= count - 1 + last)
            if not changes:
                require(visits - pending == count * last)
        else:
            require(visits == pending and last == 0)
    return data
