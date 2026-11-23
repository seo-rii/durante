"""Original schedules for the 2018 World Finals' Catch the Plane, using exact dyadic probabilities."""
from bisect import bisect_right
from collections import defaultdict
from fractions import Fraction
import random

CASES = ("layered", "simultaneous", "tight_transfers", "scattered_services")


def probability(stations, buses):
    """Solve station-local departure lists; binary search the next usable service at a destination."""
    services = defaultdict(list)
    departures = [set() for _ in range(stations)]
    for origin, destination, depart, arrive, eighths in buses:
        departures[origin].add(depart)
        services[depart, origin].append((destination, arrive, eighths))
    times = [sorted(ds) for ds in departures]
    values = [[Fraction(station == 1)] * (len(ds) + 1) for station, ds in enumerate(times)]
    for depart, origin in sorted(services, reverse=True):
        after = bisect_right(times[origin], depart)
        wait = values[origin][after]
        best = wait
        for destination, arrive, eighths in services[depart, origin]:
            next_service = bisect_right(times[destination], arrive)
            board = values[destination][next_service]
            best = max(best, (eighths * board + (8 - eighths) * wait) / 8)
        values[origin][after - 1] = best
    return values[0][0]


def make(name):
    buses = []
    if name == "layered":
        # Repeated edges between four layers; alternatives and retries coexist.
        width = 128
        stations = 2 + 4 * width
        buses.append((0, 2, 0, 1, 8))
        for step in range(14):
            for lane in range(width):
                origin = 2 + (step % 4) * width + lane
                for jump in range(8):
                    destination = 2 + (step + 1) % 4 * width + (lane + 19 * jump) % width
                    if step == 13 and (lane + jump) % 3 == 0:
                        destination = 1
                    buses.append((origin, destination, 4 * step + 2, 4 * step + 4, 1 + (lane + jump) % 7))
    elif name in ("simultaneous", "tight_transfers"):
        # Large same-station batches stress the distinction between alternative
        # departures and later retries. Some transfers miss by exactly zero time.
        tight = name == "tight_transfers"
        stations = 97 if tight else 65
        rng = random.Random(97451 if tight else 11237)
        for step in range(16):
            for origin in range(stations):
                if origin == 1:
                    continue
                for _ in range(9 if tight else 15):
                    destination = rng.randrange(stations - 1)
                    if destination >= origin:
                        destination += 1
                    depart = 3 * step
                    arrive = depart + (rng.choice((2, 3, 3)) if tight else 1)
                    buses.append((origin, destination, depart, arrive, rng.randrange(1, 8)))
        if tight:
            rng.shuffle(buses)
    elif name == "scattered_services":
        stations = 801
        rng = random.Random(37843)
        for _ in range(17_000):
            origin = rng.randrange(stations)
            destination = rng.randrange(stations - 1)
            if destination >= origin:
                destination += 1
            depart = 4 * rng.randrange(16)
            buses.append((origin, destination, depart, depart + rng.randrange(1, 10), rng.randrange(9)))
        buses.extend(((0, 2, 0, 1, 7), (2, 1, 36, 38, 3)))
    else:
        raise KeyError(name)
    deadline = max(bus[3] for bus in buses)
    header = f"{len(buses)} {stations}\n{deadline}\n"
    data = header + "".join(f"{a} {b} {s} {t} {p / 8:.3f}\n" for a, b, s, t, p in buses)
    # At most 16 choices along a path, each with denominator 8. Its exact
    # probability has a binary representation that fits in a C++ double.
    return data, f"{float(probability(stations, buses)):.9f}\n"
