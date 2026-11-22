# Same-executable path-switch control

The static C++20 control initializes 256 words and chooses one of two scalar loops from stdin. Mode A reads successive words and accumulates them. Mode B reads in reverse order and XORs each value with 90 before accumulation. The binary SHA-256 is the same for all three cases.

In the archived ELF the two load instructions are at module offsets 5894 (forward) and 5950 (reverse). Each reads one 32-bit word per iteration from a 64-byte-aligned table. The 1024-visit sampling cadence always lands on the first two words in A and the last two in B. Each completed pair is near, and the span of the sampled starting addresses is four bytes. The A calibration selects offset 5894. On a larger A input it continues to report that loop. On the B input it emits no executed selected sites. The full sampler still observes the reverse loop.

The source was compiled on x86-64 Debian with g++ 12.2.0-14+deb12u1, binutils 2.40-2 and libc6-dev 2.36-9+deb12u14. To rebuild, pass a new directory to `python3 build.py DIR` from this folder. The script verifies the ELF digest, checks each program output against a separate table-sum calculation, and creates `DIR/elf.tar.xz` and `DIR/cases.json` with the frozen contents. `objdump -d -Mintel --disassemble=forward DIR/path_switch` and the corresponding `--disassemble=reverse` command show the two distinct PCs.
