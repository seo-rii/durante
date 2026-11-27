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
const n = nextInt(), q = nextInt();
const a = new Int32Array(n);
for (let i = 0; i < n; i++) a[i] = nextInt();
a.sort();
const out = new Array(q);
for (let k = 0; k < q; k++) {
    const x = nextInt();
    let lo = 0, hi = n;
    while (lo < hi) { const mid = (lo + hi) >>> 1; if (a[mid] <= x) lo = mid + 1; else hi = mid; }
    out[k] = lo;
}
process.stdout.write(out.join('\n') + '\n');
