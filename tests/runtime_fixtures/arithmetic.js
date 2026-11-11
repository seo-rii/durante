'use strict';
// Default optimizing JIT and --jitless must preserve this result. It includes
// Math.imul/typed-array operations and repeated calls after a warmup cohort.
const values = new Int32Array(1024);
for (let i = 0; i < values.length; ++i) values[i] = i;
function sum() {
  let result = 0;
  for (let i = 0; i < values.length; ++i) result += Math.imul(values[i], 3);
  return result;
}
let result = 0;
for (let iteration = 0; iteration < 1000; ++iteration) result = sum();
console.log(result);
