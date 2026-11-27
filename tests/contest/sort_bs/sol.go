package main

import (
	"bufio"
	"os"
	"sort"
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
	a := make([]int, n)
	for i := range a {
		a[i] = readInt()
	}
	sort.Ints(a)
	out := bufio.NewWriterSize(os.Stdout, 1<<16)
	defer out.Flush()
	buf := make([]byte, 0, 16)
	for k := 0; k < q; k++ {
		x := readInt()
		idx := sort.Search(n, func(i int) bool { return a[i] > x })
		buf = strconv.AppendInt(buf[:0], int64(idx), 10)
		buf = append(buf, '\n')
		out.Write(buf)
	}
}
