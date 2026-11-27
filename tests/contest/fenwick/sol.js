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
// Sums stay below 2^53 (at most 1.4e6 * 1e9), so doubles are exact.
const n = nextInt(), q = nextInt();
const bit = new Float64Array(n + 1);
for (let i = 1; i <= n; i++) {
    bit[i] += nextInt();
    const j = i + (i & -i);
    if (j <= n) bit[j] += bit[i];
}
const out = [];
for (let k = 0; k < q; k++) {
    const t = nextInt(), x = nextInt(), y = nextInt();
    if (t === 1) {
        for (let i = x; i <= n; i += i & -i) bit[i] += y;
    } else {
        let s = 0;
        for (let i = y; i > 0; i -= i & -i) s += bit[i];
        for (let i = x - 1; i > 0; i -= i & -i) s -= bit[i];
        out.push(s);
    }
}
process.stdout.write(out.join('\n') + '\n');
