'use strict';
// Recursion depth n: the harness runs node with a raised --stack-size and an
// unlimited stack rlimit, the usual way contest judges allow deep recursion.
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
const n = nextInt();
const head = new Int32Array(n + 1).fill(-1), nxt = new Int32Array(2 * (n - 1)), to = new Int32Array(2 * (n - 1));
for (let i = 0, e = 0; i < n - 1; i++) {
    const u = nextInt(), v = nextInt();
    to[e] = v; nxt[e] = head[u]; head[u] = e++;
    to[e] = u; nxt[e] = head[v]; head[v] = e++;
}
let maxDepth = 0, total = 0;
function dfs(v, parent, depth) {
    if (depth > maxDepth) maxDepth = depth;
    let size = 1;
    for (let e = head[v]; e !== -1; e = nxt[e]) if (to[e] !== parent) size += dfs(to[e], v, depth + 1);
    // size * v < 1e12 < 2^53, so the product is exact before reduction.
    total = (total + (size * v) % 1000000007) % 1000000007;
    return size;
}
dfs(1, 0, 0);
console.log(maxDepth + ' ' + total);
