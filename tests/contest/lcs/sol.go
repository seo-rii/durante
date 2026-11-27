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
	a, b := readToken(), readToken()
	m := len(b)
	prev, cur := make([]int32, m+1), make([]int32, m+1)
	for _, ca := range a {
		cur[0] = 0
		for j := 1; j <= m; j++ {
			if ca == b[j-1] {
				cur[j] = prev[j-1] + 1
			} else if prev[j] > cur[j-1] {
				cur[j] = prev[j]
			} else {
				cur[j] = cur[j-1]
			}
		}
		prev, cur = cur, prev
	}
	fmt.Println(prev[m])
}
