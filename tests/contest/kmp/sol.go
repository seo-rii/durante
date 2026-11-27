package main

import (
	"bufio"
	"fmt"
	"os"
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
	t, p := readToken(), readToken()
	m := len(p)
	fail := make([]int, m)
	for i, k := 1, 0; i < m; i++ {
		for k > 0 && p[i] != p[k] {
			k = fail[k-1]
		}
		if p[i] == p[k] {
			k++
		}
		fail[i] = k
	}
	var count, total int64
	for i, k := 0, 0; i < len(t); i++ {
		for k > 0 && t[i] != p[k] {
			k = fail[k-1]
		}
		if t[i] == p[k] {
			k++
		}
		if k == m {
			count++
			total += int64(i - m + 2)
			k = fail[k-1]
		}
	}
	fmt.Println(count, total)
}
