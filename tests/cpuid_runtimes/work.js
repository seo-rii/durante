const crypto = require('crypto');
const n = Number(process.argv[2]);
const v = new Float64Array(n); let x = 42;
for (let i = 0; i < n; i++) { x = (x * 1103515245 + 12345) % 2147483648; v[i] = x; }
v.sort();
let s = 0; for (let i = 0; i < n; i++) s = (s + v[i] * (i + 1)) % 1e15;
const d = Buffer.alloc(1 << 20); for (let i = 0; i < d.length; i++) d[i] = i & 255;
console.log(s, crypto.createHash('sha256').update(d).digest('hex'));
