"""Fixed CPython workloads; all child output is part of the fixture oracle."""
import gc
from pathlib import Path
import subprocess
import sys
import threading


def threads():
    ready = threading.Barrier(3, timeout=10)
    values, errors = [0, 0], []

    def worker(index):
        try:
            ready.wait()
            for item in range(1, 10001):
                values[index] += item * (index + 1)
        except BaseException as error:
            errors.append(error)

    workers = [threading.Thread(target=worker, args=(index,)) for index in range(2)]
    for worker in workers:
        worker.start()
    ready.wait()
    for worker in workers:
        worker.join(10)
        if worker.is_alive():
            raise RuntimeError("worker deadline")
    if errors:
        raise errors[0]
    print("threads:" + str(sum(values)))


def allocation_gc():
    gc.disable()
    checksum = collected = 0
    live = [None] * 128
    for round_number in range(16):
        for item in range(128):
            block = bytearray(1024)
            value = (round_number + item) & 255
            block[0], block[-1] = value, 255 - value
            live[item] = block
            cycle = [block]
            cycle.append(cycle)
            checksum += block[0] + block[-1]
        if round_number % 4 == 3:
            collected += gc.collect()
    if collected <= 0:
        raise RuntimeError("no cyclic collection observed")
    retained = 0
    for item, block in enumerate(live):
        expected = (15 + item) & 255
        if len(block) != 1024 or block[0] != expected or block[-1] != 255 - expected:
            raise RuntimeError("retained allocation changed after collection")
        retained += block[0] + block[-1]
    print("allocation-gc:" + str(checksum) + ":retained:" + str(retained) + ":observed")


def processes():
    total = 0
    for seed in (7, 11):
        result = subprocess.run([sys.executable, "-I", "-S", str(Path(__file__).resolve()),
                                 "child", str(seed)], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15, check=True)
        expected = seed * seed + 17
        if result.stdout != ("child:" + str(expected) + "\n").encode() or result.stderr:
            raise RuntimeError("child output mismatch")
        total += expected
    print("processes:" + str(total))


if __name__ == "__main__":
    mode = sys.argv[1]
    if mode == "threads":
        threads()
    elif mode == "allocation-gc":
        allocation_gc()
    elif mode == "processes":
        processes()
    elif mode == "child":
        seed = int(sys.argv[2])
        print("child:" + str(seed * seed + 17))
    else:
        raise ValueError("unknown fixture")
