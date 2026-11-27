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
	h, w := readInt(), readInt()
	g := make([]byte, 0, h*w)
	for i := 0; i < h; i++ {
		g = append(g, readToken()...)
	}
	dist := make([]int32, h*w)
	for i := range dist {
		dist[i] = -1
	}
	q := make([]int32, 0, h*w)
	if g[0] == '.' {
		dist[0] = 0
		q = append(q, 0)
	}
	for head := 0; head < len(q); head++ {
		cur := int(q[head])
		r, c := cur/w, cur%w
		d := dist[cur] + 1
		try := func(ok bool, nb int) {
			if ok && g[nb] == '.' && dist[nb] < 0 {
				dist[nb] = d
				q = append(q, int32(nb))
			}
		}
		try(r > 0, cur-w)
		try(r+1 < h, cur+w)
		try(c > 0, cur-1)
		try(c+1 < w, cur+1)
	}
	fmt.Println(dist[h*w-1], len(q))
}
