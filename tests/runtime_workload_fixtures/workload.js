'use strict';
const { Worker, isMainThread, parentPort, workerData } = require('node:worker_threads');
const { spawnSync } = require('node:child_process');
const { PerformanceObserver } = require('node:perf_hooks');

async function threads() {
  const workers = [];
  let readyCount = 0;
  let started = false;
  try {
    const values = await Promise.all([0, 1].map(index => new Promise((resolve, reject) => {
      const worker = new Worker(__filename, { workerData: index, resourceLimits: {
        maxOldGenerationSizeMb: 32, maxYoungGenerationSizeMb: 2, codeRangeSizeMb: 16, stackSizeMb: 1,
      } });
      workers.push(worker);
      let ready = false;
      let value;
      worker.on('message', message => {
        if (message === 'ready' && !ready && !started) {
          ready = true;
          if (++readyCount === 2) {
            // Both workers keep their start listener alive until this barrier opens.
            started = true;
            for (const peer of workers) peer.postMessage('start');
          }
        } else if (started && value === undefined && message === 50005000 * (index + 1)) {
          value = message;
        } else {
          reject(new Error('worker message mismatch: index=' + index));
        }
      });
      worker.once('error', reject);
      worker.once('messageerror', reject);
      worker.once('exit', code => {
        if (code !== 0 || value === undefined)
          reject(new Error('worker output or exit mismatch: index=' + index + ' exit=' + code));
        else resolve(value);
      });
    })));
    console.log('threads:' + values.reduce((left, right) => left + right, 0));
  } finally {
    // A failed startup or constructor must not leave its peer waiting for start.
    await Promise.all(workers.map(worker => worker.terminate()));
  }
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
  parentPort.once('message', message => {
    if (message !== 'start') throw new Error('worker start message mismatch');
    let value = 0;
    for (let item = 1; item <= 10000; ++item) value += item * (workerData + 1);
    parentPort.postMessage(value);
    parentPort.close();
  });
  parentPort.postMessage('ready');
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
