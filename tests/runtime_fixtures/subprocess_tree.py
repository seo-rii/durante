"""Default close_fds subprocess path must preserve parent and child accounting."""
import subprocess
import sys

child = subprocess.run([sys.executable, "-I", "-S", "-c", "print(7)"],
                       check=True, capture_output=True, text=True, timeout=10)
assert child.stdout == "7\n" and child.stderr == ""
print("child:7")
