"""Exercise real Node workers, their startup barrier, and failed-peer cleanup."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from runtime_workloads import FIXTURES, NODE_FLAGS, OUTPUTS


# Delay worker 1 until worker 0 actually posts a message. The original fixture
# sends its result first, so it deterministically records an early result; a
# readiness barrier lets worker 1 start before either result is calculated.
OVERLAP_PRELOAD = r"""
const threads = require('node:worker_threads');
const fs = require('node:fs');
if (threads.isMainThread) {
  const state = new Int32Array(new SharedArrayBuffer(4 * Int32Array.BYTES_PER_ELEMENT));
  threads.setEnvironmentData('durante-test-overlap', state.buffer);
  process.on('exit', () => fs.writeFileSync(process.env.DURANTE_NODE_TEST_REPORT,
    JSON.stringify({started: Atomics.load(state, 1), results: Atomics.load(state, 2),
      earlyResult: Atomics.load(state, 3)})));
} else {
  const state = new Int32Array(threads.getEnvironmentData('durante-test-overlap'));
  if (threads.workerData === 1 && Atomics.wait(state, 0, 0, 10000) === 'timed-out')
    throw new Error('test worker 0 never posted a message');
  Atomics.add(state, 1, 1);
  const postMessage = threads.parentPort.postMessage.bind(threads.parentPort);
  threads.parentPort.postMessage = message => {
    if (typeof message === 'number') {
      Atomics.add(state, 2, 1);
      if (Atomics.load(state, 1) !== 2) Atomics.store(state, 3, 1);
    }
    postMessage(message);
    if (threads.workerData === 0) {
      Atomics.store(state, 0, 1);
      Atomics.notify(state, 0);
    }
  };
}
"""


# Substitute real worker scripts, retaining the fixture's parent and resource
# limits. The unaffected peer deliberately keeps its message port alive forever:
# a parent that rejects but fails to terminate that peer hits the outer deadline.
FAULT_PRELOAD = r"""
const threads = require('node:worker_threads');
const fs = require('node:fs');
if (threads.isMainThread) {
  const report = {created: [], exited: []};
  const fault = process.env.DURANTE_NODE_TEST_FAULT;
  const failedIndex = Number(process.env.DURANTE_NODE_TEST_INDEX);
  const Worker = threads.Worker;
  threads.Worker = class extends Worker {
    constructor(filename, options) {
      const index = options.workerData;
      if (fault === 'constructor' && index === 1)
        throw new Error('injected worker constructor failure');
      const common = "const {parentPort, workerData} = require('node:worker_threads');\n";
      let script = "parentPort.on('message', () => {}); parentPort.postMessage('ready');";
      if (index === failedIndex) {
        if (fault === 'startup-error') script = "throw new Error('injected worker startup failure');";
        if (fault === 'exit-zero') script = 'process.exit(0);';
        if (fault === 'exit-seven') script = 'process.exit(7);';
        if (fault === 'early-result')
          script = 'parentPort.postMessage(50005000 * (workerData + 1)); parentPort.close();';
        if (fault === 'duplicate-ready')
          script = "parentPort.on('message', () => {}); parentPort.postMessage('ready'); parentPort.postMessage('ready');";
        if (['missing-result', 'wrong-result', 'duplicate-result'].includes(fault)) {
          let result = '';
          if (fault === 'wrong-result') result = 'parentPort.postMessage(50005000 * (workerData + 1) + 1);';
          if (fault === 'duplicate-result')
            result = 'parentPort.postMessage(50005000 * (workerData + 1)); parentPort.postMessage(50005000 * (workerData + 1));';
          script = "parentPort.once('message', () => { " + result
            + " parentPort.close(); }); parentPort.postMessage('ready');";
        }
      }
      super(common + script, {...options, eval: true});
      report.created.push(index);
      this.once('exit', code => report.exited.push({index, code}));
    }
  };
  process.on('exit', () => fs.writeFileSync(process.env.DURANTE_NODE_TEST_REPORT, JSON.stringify(report)));
}
"""


class NodeThreadFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.node = shutil.which('node')
        if not cls.node:
            raise unittest.SkipTest('native Node fixture requires node')

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='durante-node-thread-unit-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def run_fixture(self, mode='threads', *, preload=None, fault=None, index=0, fixture=None):
        command = [self.node, *NODE_FLAGS]
        environment = dict(os.environ)
        report_path = self.root / 'report.json'
        report_path.unlink(missing_ok=True)
        if preload is not None:
            preload_path = self.root / 'preload.cjs'
            preload_path.write_text(preload)
            command += ['--require', str(preload_path)]
            environment['DURANTE_NODE_TEST_REPORT'] = str(report_path)
        if fault is not None:
            environment['DURANTE_NODE_TEST_FAULT'] = fault
            environment['DURANTE_NODE_TEST_INDEX'] = str(index)
        command += [str(fixture or FIXTURES / 'workload.js'), mode]
        result = subprocess.run(command, env=environment, capture_output=True,
                                text=True, timeout=20, check=False)
        report = json.loads(report_path.read_text()) if preload is not None else None
        return result, report

    def test_native_workloads_keep_their_exact_outputs(self):
        for mode, expected in OUTPUTS.items():
            with self.subTest(mode=mode):
                result, _ = self.run_fixture(mode)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(result.stdout, expected)
                self.assertEqual(result.stderr, '')

    def test_both_real_workers_start_before_either_posts_a_result(self):
        result, report = self.run_fixture(preload=OVERLAP_PRELOAD)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, OUTPUTS['threads'])
        self.assertEqual(result.stderr, '')
        self.assertEqual(report, {'started': 2, 'results': 2, 'earlyResult': 0})

    def assert_failed_and_joined(self, fault, index, expected_error, *, created=(0, 1)):
        result, report = self.run_fixture(preload=FAULT_PRELOAD, fault=fault, index=index)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, '')
        self.assertIn(expected_error, result.stderr)
        self.assertEqual(report['created'], list(created))
        self.assertEqual(sorted(row['index'] for row in report['exited']), list(created))

    def test_startup_errors_terminate_the_waiting_peer(self):
        for index in (0, 1):
            with self.subTest(index=index):
                self.assert_failed_and_joined('startup-error', index, 'injected worker startup failure')

    def test_exit_before_readiness_terminates_the_waiting_peer(self):
        for index in (0, 1):
            for fault, code in (('exit-zero', 0), ('exit-seven', 7)):
                with self.subTest(index=index, code=code):
                    self.assert_failed_and_joined(fault, index,
                        f'worker output or exit mismatch: index={index} exit={code}')

    def test_exit_after_start_without_result_terminates_the_waiting_peer(self):
        for index in (0, 1):
            with self.subTest(index=index):
                self.assert_failed_and_joined('missing-result', index,
                    f'worker output or exit mismatch: index={index} exit=0')

    def test_out_of_order_duplicate_and_incorrect_messages_fail(self):
        for index in (0, 1):
            for fault in ('early-result', 'duplicate-ready', 'wrong-result', 'duplicate-result'):
                with self.subTest(index=index, fault=fault):
                    self.assert_failed_and_joined(fault, index, f'worker message mismatch: index={index}')

    def test_second_constructor_failure_terminates_the_first_worker(self):
        self.assert_failed_and_joined('constructor', 1, 'injected worker constructor failure', created=(0,))


if __name__ == '__main__':
    unittest.main()
