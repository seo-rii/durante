'use strict';
const { Worker, isMainThread, parentPort, workerData } = require('node:worker_threads');
const { spawnSync } = require('node:child_process');
const { PerformanceObserver } = require('node:perf_hooks');

async function threads() {
  const values = await Promise.all([0, 1].map(index => new Promise((resolve, reject) => {
    const worker = new Worker(__filename, { workerData: index, resourceLimits: {
      maxOldGenerationSizeMb: 32, maxYoungGenerationSizeMb: 2, codeRangeSizeMb: 16, stackSizeMb: 1,
    } });
    let value;
    worker.on('message', message => { value = message; });
    worker.on('error', reject);
    worker.on('exit', code => {
      if (code !== 0 || !Number.isSafeInteger(value)) reject(new Error('worker output or exit mismatch'));
      else resolve(value);
    });
  })));
  console.log('threads:' + values.reduce((left, right) => left + right, 0));
}

async function allocationGc() {
  let events = 0;
  const observer = new PerformanceObserver(list => { events += list.getEntries().length; });
  observer.observe({ entryTypes: ['gc'] });
  let checksum = 0;
  const live = new Array(128);
  for (let round = 0; round < 16; ++round) {
    for (let item = 0; item < live.length; ++item) {
      const block = new Uint8Array(1024);
      const value = (round + item) & 255;
      block[0] = value;
      block[1023] = 255 - value;
      live[item] = block;
      checksum += block[0] + block[1023];
    }
    if (round % 4 === 3) global.gc();
  }
  await new Promise(resolve => setImmediate(resolve));
  await new Promise(resolve => setImmediate(resolve));
  observer.disconnect();
  if (events === 0) throw new Error('no garbage collection observed');
  let retained = 0;
  for (let item = 0; item < live.length; ++item) {
    const expected = (15 + item) & 255;
    if (live[item].length !== 1024 || live[item][0] !== expected || live[item][1023] !== 255 - expected)
      throw new Error('retained allocation changed after collection');
    retained += live[item][0] + live[item][1023];
  }
  console.log('allocation-gc:' + checksum + ':retained:' + retained + ':observed');
}

function processes() {
  let total = 0;
  for (const seed of [7, 11]) {
    const child = spawnSync(process.execPath, [...process.execArgv, __filename, 'child', String(seed)],
                            { encoding: 'utf8', timeout: 15000, maxBuffer: 1024 });
    const expected = seed * seed + 17;
    if (child.error || child.signal || child.status !== 0 || child.stderr || child.stdout !== 'child:' + expected + '\n')
      throw new Error('child output or exit mismatch');
    total += expected;
  }
  console.log('processes:' + total);
}

if (!isMainThread) {
  let value = 0;
  for (let item = 1; item <= 10000; ++item) value += item * (workerData + 1);
  parentPort.postMessage(value);
} else {
  Promise.resolve().then(() => {
    const mode = process.argv[2];
    if (mode === 'threads') return threads();
    if (mode === 'allocation-gc') return allocationGc();
    if (mode === 'processes') return processes();
    if (mode === 'child') {
      const seed = Number(process.argv[3]);
      console.log('child:' + (seed * seed + 17));
      return;
    }
    throw new Error('unknown fixture');
  }).catch(error => { console.error(error); process.exitCode = 1; });
}
