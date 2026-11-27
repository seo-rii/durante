package main

import (
	"bufio"
	"container/heap"
	"fmt"
	"math"
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

type item struct {
	d int64
	v int32
}
type pq []item

func (p pq) Len() int            { return len(p) }
func (p pq) Less(i, j int) bool  { return p[i].d < p[j].d }
func (p pq) Swap(i, j int)       { p[i], p[j] = p[j], p[i] }
func (p *pq) Push(x interface{}) { *p = append(*p, x.(item)) }
func (p *pq) Pop() interface{} {
	old := *p
	it := old[len(old)-1]
	*p = old[:len(old)-1]
	return it
}

func main() {
	n, m := readInt(), readInt()
	eu, ev, ew := make([]int32, m), make([]int32, m), make([]int32, m)
	start := make([]int32, n+2)
	for i := 0; i < m; i++ {
		eu[i], ev[i], ew[i] = int32(readInt()), int32(readInt()), int32(readInt())
		start[eu[i]+1]++
	}
	for i := 1; i <= n+1; i++ {
		start[i] += start[i-1]
	}
	to, wt := make([]int32, m), make([]int32, m)
	fill := append([]int32(nil), start...)
	for i := 0; i < m; i++ {
		p := fill[eu[i]]
		fill[eu[i]]++
		to[p], wt[p] = ev[i], ew[i]
	}
	dist := make([]int64, n+1)
	for i := range dist {
		dist[i] = math.MaxInt64
	}
	dist[1] = 0
	h := &pq{{0, 1}}
	var reach, sum int64
	for h.Len() > 0 {
		it := heap.Pop(h).(item)
		if it.d != dist[it.v] {
			continue
		}
		reach++
		sum += it.d
		for p := start[it.v]; p < start[it.v+1]; p++ {
			nd := it.d + int64(wt[p])
			if nd < dist[to[p]] {
				dist[to[p]] = nd
				heap.Push(h, item{nd, to[p]})
			}
		}
	}
	dn := dist[n]
	if dn == math.MaxInt64 {
		dn = -1
	}
	fmt.Println(dn, reach, sum)
}
