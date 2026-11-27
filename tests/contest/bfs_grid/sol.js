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
const h = nextInt(), w = nextInt();
const g = new Uint8Array(h * w);
for (let i = 0; i < h; i++) g.set(nextToken(), i * w);
const dist = new Int32Array(h * w).fill(-1);
const q = new Int32Array(h * w);
let head = 0, tail = 0;
if (g[0] === 46) { dist[0] = 0; q[tail++] = 0; }
while (head < tail) {
    const cur = q[head++], r = (cur / w) | 0, c = cur - r * w, d = dist[cur] + 1;
    if (r > 0 && g[cur - w] === 46 && dist[cur - w] < 0) { dist[cur - w] = d; q[tail++] = cur - w; }
    if (r + 1 < h && g[cur + w] === 46 && dist[cur + w] < 0) { dist[cur + w] = d; q[tail++] = cur + w; }
    if (c > 0 && g[cur - 1] === 46 && dist[cur - 1] < 0) { dist[cur - 1] = d; q[tail++] = cur - 1; }
    if (c + 1 < w && g[cur + 1] === 46 && dist[cur + 1] < 0) { dist[cur + 1] = d; q[tail++] = cur + 1; }
}
console.log(dist[h * w - 1] + ' ' + tail);
