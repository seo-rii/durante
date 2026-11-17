import hashlib, sys, zlib
n = int(sys.argv[1])
d = bytes(range(256)) * (4096 * n)
print(hashlib.sha256(d).hexdigest(), len(zlib.compress(d, 6)), sum(sorted((i * 2654435761) % 1000003 for i in range(20000 * n))) % 1000000007)
