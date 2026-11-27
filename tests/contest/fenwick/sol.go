package main

import (
	"bufio"
	"os"
	"strconv"
)

var rd = bufio.NewReaderSize(os.Stdin, 1<<16)

func readInt() int {
	c, _ := rd.ReadByte()
	for c != 45 && (c < 48 || c > 57) {
		c, _ = rd.ReadByte()
	}
	neg := c == 45
	if neg {
		c, _ = rd.ReadByte()
	}
	x := 0
	for c >= 48 && c <= 57 {
		x = x*10 + int(c-48)
		var err error
		if c, err = rd.ReadByte(); err != nil {
			break
		}
	}
	if neg {
		return -x
	}
	return x
}

func readToken() []byte {
	c, _ := rd.ReadByte()
	for c <= 32 {
		c, _ = rd.ReadByte()
	}
	var out []byte
	for c > 32 {
		out = append(out, c)
		var err error
		if c, err = rd.ReadByte(); err != nil {
			break
		}
	}
	return out
}

func main() {
	n, q := readInt(), readInt()
	bit := make([]int64, n+1)
	for i := 1; i <= n; i++ {
		bit[i] += int64(readInt())
		if j := i + (i & -i); j <= n {
			bit[j] += bit[i]
		}
	}
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	defer out.Flush()
	buf := make([]byte, 0, 24)
	for k := 0; k < q; k++ {
		t, x, y := readInt(), readInt(), readInt()
		if t == 1 {
			for i := x; i <= n; i += i & -i {
				bit[i] += int64(y)
			}
		} else {
			var s int64
			for i := y; i > 0; i -= i & -i {
				s += bit[i]
			}
			for i := x - 1; i > 0; i -= i & -i {
				s -= bit[i]
			}
			buf = strconv.AppendInt(buf[:0], s, 10)
			buf = append(buf, '\n')
			out.Write(buf)
		}
	}
}
