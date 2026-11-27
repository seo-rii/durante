'use strict';
const data = require('fs').readFileSync(0);
let pos = 0;
function nextInt() {
    while (data[pos] !== 45 && (data[pos] < 48 || data[pos] > 57)) pos++;
    let neg = false;
    if (data[pos] === 45) { neg = true; pos++; }
    let x = 0;
    while (pos < data.length && data[pos] >= 48 && data[pos] <= 57) x = x * 10 + (data[pos++] - 48);
    return neg ? -x : x;
}
function nextToken() {
    while (data[pos] <= 32) pos++;
    const s = pos;
    while (pos < data.length && data[pos] > 32) pos++;
    return data.subarray(s, pos);
}
const n = nextInt(), m = nextInt();
const eu = new Int32Array(m), ev = new Int32Array(m), ew = new Int32Array(m), start = new Int32Array(n + 2);
for (let i = 0; i < m; i++) { eu[i] = nextInt(); ev[i] = nextInt(); ew[i] = nextInt(); start[eu[i] + 1]++; }
for (let i = 1; i <= n + 1; i++) start[i] += start[i - 1];
const to = new Int32Array(m), wt = new Int32Array(m), fill = Int32Array.from(start);
for (let i = 0; i < m; i++) { const p = fill[eu[i]]++; to[p] = ev[i]; wt[p] = ew[i]; }
const dist = new Float64Array(n + 1).fill(Infinity);
const hd = new Float64Array(m + 1), hv = new Int32Array(m + 1);
let size = 1, reach = 0, sum = 0;
dist[1] = 0; hd[0] = 0; hv[0] = 1;
while (size > 0) {
    const d = hd[0], u = hv[0];
    size--;
    if (size > 0) {
        const ld = hd[size], lv = hv[size];
        let i = 0;
        for (;;) {
            let c = 2 * i + 1;
            if (c >= size) break;
            if (c + 1 < size && hd[c + 1] < hd[c]) c++;
            if (hd[c] >= ld) break;
            hd[i] = hd[c]; hv[i] = hv[c]; i = c;
        }
        hd[i] = ld; hv[i] = lv;
    }
    if (d !== dist[u]) continue;
    reach++;
    sum += d;
    for (let p = start[u]; p < start[u + 1]; p++) {
        const nd = d + wt[p], v = to[p];
        if (nd < dist[v]) {
            dist[v] = nd;
            let i = size++;
            while (i > 0 && hd[(i - 1) >> 1] > nd) { const pa = (i - 1) >> 1; hd[i] = hd[pa]; hv[i] = hv[pa]; i = pa; }
            hd[i] = nd; hv[i] = v;
        }
    }
}
console.log((dist[n] === Infinity ? -1 : dist[n]) + ' ' + reach + ' ' + sum);
