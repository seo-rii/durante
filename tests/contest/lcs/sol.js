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
const a = nextToken(), b = nextToken(), m = b.length;
let prev = new Int32Array(m + 1), cur = new Int32Array(m + 1);
for (let i = 0; i < a.length; i++) {
    const ca = a[i];
    cur[0] = 0;
    for (let j = 1; j <= m; j++) {
        if (ca === b[j - 1]) cur[j] = prev[j - 1] + 1;
        else cur[j] = prev[j] > cur[j - 1] ? prev[j] : cur[j - 1];
    }
    const t = prev; prev = cur; cur = t;
}
console.log(String(prev[m]));
