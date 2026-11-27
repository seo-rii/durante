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
const t = nextToken(), p = nextToken(), m = p.length;
const fail = new Int32Array(m);
for (let i = 1, k = 0; i < m; i++) {
    while (k > 0 && p[i] !== p[k]) k = fail[k - 1];
    if (p[i] === p[k]) k++;
    fail[i] = k;
}
let count = 0, total = 0;
for (let i = 0, k = 0; i < t.length; i++) {
    while (k > 0 && t[i] !== p[k]) k = fail[k - 1];
    if (t[i] === p[k]) k++;
    if (k === m) { count++; total += i - m + 2; k = fail[k - 1]; }
}
console.log(count + ' ' + total);
