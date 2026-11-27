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

const mod = 1000000007

var head, nxt, to []int32
var total int64
var maxDepth int

// Goroutine stacks grow on demand (1 GB default maximum), so plain recursion works.
func dfs(v, parent int32, depth int) int64 {
	if depth > maxDepth {
		maxDepth = depth
	}
	size := int64(1)
	for e := head[v]; e != -1; e = nxt[e] {
		if to[e] != parent {
			size += dfs(to[e], v, depth+1)
		}
	}
	total = (total + size%mod*int64(v)) % mod
	return size
}

func main() {
	n := readInt()
	head = make([]int32, n+1)
	for i := range head {
		head[i] = -1
	}
	nxt, to = make([]int32, 2*(n-1)), make([]int32, 2*(n-1))
	for i, e := 0, int32(0); i < n-1; i++ {
		u, v := int32(readInt()), int32(readInt())
		to[e], nxt[e], head[u] = v, head[u], e
		e++
		to[e], nxt[e], head[v] = u, head[v], e
		e++
	}
	dfs(1, 0, 0)
	fmt.Println(maxDepth, total)
}
