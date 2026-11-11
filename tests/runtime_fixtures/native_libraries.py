"""Exercise stock C extension/library paths, not only interpreter bytecodes."""
import hashlib
import zlib

payload = b"abc" * 10000
compressed = zlib.compress(payload)
assert zlib.decompress(compressed) == payload
print(hashlib.sha256(b"abc").hexdigest())
print(len(zlib.decompress(compressed)))
