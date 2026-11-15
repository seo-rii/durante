import copy
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector',ROOT/'collect.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


def event(pid,segment,seq,kind,**extra):
 return dict(schema=1,client_version=1,authority='diagnostic_only',metric='bb_entry_app_instructions',
  pid=pid,segment=f'{pid}-{segment}',seq=seq,event=kind,**extra)

def start(pid,segment=1,origin='initial',parent=0,parent_segment=''):
 return event(pid,segment,1,'start',origin=origin,parent_pid=parent,parent_segment=parent_segment,
  rep_bytes_enabled=False,sdk_version='11.3.0')

def end(pid,segment=1,seq=2,kind='end',count=100,code=0,signals=0):
 return event(pid,segment,seq,kind,instructions=count,rep_bytes=0,rep_entries=0,unsupported_rep_entries=0,
  signals_observed=signals,threads_started=1,threads_finished=1,threads_complete=True,
  accounting_error=False,app_exit_seen=kind=='end',app_exit_code=code,valid=True,
  quality='signals_observed' if signals else 'uninterrupted')

def fork_tree():
 return {1:[start(1),event(1,1,2,'spawn',child_pid=2,syscall=57,supported=True),end(1,seq=3,count=100)],
         2:[start(2,origin='fork',parent=1,parent_segment='1-1'),end(2,count=30)]}


class CollectorTests(unittest.TestCase):
 def test_claimed_trust_and_coverage_are_not_copied_from_records(self):
  for enabled,unsupported,expected in [(False,0,'disabled'),(True,0,'movs_stos_only'),(True,3,'partial')]:
   rows=[start(1),end(1)]
   rows[0]['rep_bytes_enabled']=enabled;rows[1]['unsupported_rep_entries']=unsupported
   for row in rows:row.update(integrity_verified=True,trusted_verdict_eligible=True,rep_telemetry_status='complete')
   a=m.aggregate({1:rows},1)
   self.assertTrue(a['valid']);self.assertEqual(a['instructions'],100)
   self.assertIs(a['integrity_verified'],False)
   self.assertEqual(a['measurement_contract'],{'version':1,'backend':'dynamorio',
    'counter_integrity':'application_mutable','trusted_verdict_eligible':False,
    'instruction_semantics':'basic_block_entry','simd_weighting':'one_per_instruction','cpu_time_conversion':None})
   self.assertEqual(a['rep_telemetry_status'],expected)
  with tempfile.TemporaryDirectory() as d:
   a=m.collect(Path(d),1)
   self.assertFalse(a['valid']);self.assertIs(a['integrity_verified'],False)
   self.assertIs(a['measurement_contract']['trusted_verdict_eligible'],False)
   self.assertEqual(a['rep_telemetry_status'],'unavailable')
 def test_shared_protocol_vectors(self):
  vectors=json.loads((ROOT/'tests/protocol_vectors.json').read_text())['cases']
  for case in vectors:
   streams={int(name[8:-6]):rows for name,rows in case['files'].items()}
   if case['valid']:
    result=m.aggregate(streams,case['root_pid'])
    self.assertEqual({k:result[k] for k in case['expected']},case['expected'],case['name'])
   else:
    with self.assertRaises(m.Invalid,msg=case['name']):m.aggregate(streams,case['root_pid'])
 def test_one_process(self):
  a=m.aggregate({1:[start(1),end(1)]},1);self.assertEqual(a['instructions'],100)
 def test_fork_sums_once(self):
  a=m.aggregate(fork_tree(),1);self.assertEqual(a['instructions'],130);self.assertEqual(a['process_count'],2)
 def test_successful_exec_sums_segments(self):
  rows=[start(1),end(1,kind='exec_attempt',count=100),start(1,2),end(1,2,count=25)]
  a=m.aggregate({1:rows},1);self.assertEqual(a['instructions'],125);self.assertEqual(a['image_count'],2)
 def test_failed_exec_does_not_duplicate_checkpoint(self):
  rows=[start(1),end(1,kind='exec_attempt',count=100),event(1,1,3,'exec_failed',syscall=59,result=-2),end(1,seq=4,count=125)]
  self.assertEqual(m.aggregate({1:rows},1)['instructions'],125)
 def test_nonzero_and_signals_are_explicit(self):
  a=m.aggregate({1:[start(1),end(1,code=7,signals=1)]},1)
  self.assertTrue(a['valid']);self.assertEqual(a['processes'][0]['exit_code'],7)
  self.assertEqual(a['quality'],'signals_observed')
 def test_missing_child_fails(self):
  rows=fork_tree();del rows[2]
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_killed_child_fails(self):
  rows=fork_tree();rows[2].pop()
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_unannounced_child_fails(self):
  rows=fork_tree();rows[1].pop(1);rows[1][-1]['seq']=2
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_duplicate_child_fails(self):
  rows=fork_tree();rows[1].insert(2,event(1,1,3,'spawn',child_pid=2,syscall=57,supported=True));rows[1][-1]['seq']=4
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_lineage_mismatch_fails(self):
  rows=fork_tree();rows[2][0]['parent_segment']='1-99'
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_duplicate_sequence_fails(self):
  with self.assertRaises(m.Invalid):m.aggregate({1:[start(1),end(1,seq=1)]},1)
 def test_pid_reuse_fails(self):
  with self.assertRaises(m.Invalid):m.aggregate({1:[start(1),end(1),start(1,2),end(1,2)]},1)
 def test_unclosed_exec_fails(self):
  with self.assertRaises(m.Invalid):m.aggregate({1:[start(1),end(1,kind='exec_attempt')]},1)
 def test_multithread_exec_fails(self):
  checkpoint=end(1,kind='exec_attempt');checkpoint['valid']=False
  with self.assertRaises(m.Invalid):m.aggregate({1:[start(1),checkpoint,start(1,2),end(1,2)]},1)
 def test_shared_vm_spawn_fails(self):
  rows=fork_tree();rows[1][1]['supported']=False
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_tree_overflow_fails(self):
  rows=fork_tree();rows[1][-1]['instructions']=m.U64_MAX
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_bool_counter_and_mixed_modes_fail(self):
  rows=fork_tree();rows[2][-1]['instructions']=True
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
  rows=fork_tree();rows[2][0]['rep_bytes_enabled']=True
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_disconnected_cycle_fails(self):
  rows=fork_tree();rows[2][0]['parent_pid']=2
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_hardened_file_reads(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);f=p/'process-1.jsonl';f.write_text(''.join(json.dumps(x)+'\n' for x in [start(1),end(1)]))
   self.assertTrue(m.collect(p,1)['valid'])
   data=f.read_text();f.write_text(data[:-1]);self.assertFalse(m.collect(p,1)['valid'])
   f.unlink();f.symlink_to('/dev/zero');self.assertFalse(m.collect(p,1)['valid'])
 def test_duplicate_json_key_and_oversize_fail(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);f=p/'process-1.jsonl';f.write_text('{"pid":1,"pid":1}\n')
   self.assertFalse(m.collect(p,1)['valid'])
   f.write_bytes(b'x'*(m.MAX_FILE_BYTES+1));self.assertFalse(m.collect(p,1)['valid'])
 def test_directory_symlink_fails(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);(p/'link').symlink_to(p,target_is_directory=True)
   self.assertFalse(m.collect(p/'link',1)['valid'])
 def test_boolean_protocol_integer_rejected(self):
  for field in ['schema','client_version','pid','seq']:
   rows={1:[start(1),end(1)]};rows[1][0][field]=True
   with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_directory_entry_count_bounded(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)
   for i in range(m.MAX_PROCESSES+1):(p/f'process-{i+1}.jsonl').touch()
   self.assertFalse(m.collect(p,1)['valid'])
 def test_file_count_bound(self):
  with self.assertRaises(m.Invalid):m.aggregate({i:[start(i),end(i)] for i in range(1,258)},1)

 def test_flat_grammar_and_deep_nesting_fail_closed(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d);f=p/'process-1.jsonl'
   valid=''.join(json.dumps(x)+'\n' for x in [start(1),end(1)])
   for raw in [valid.replace('\n','\r\n'),
               '{"pid":1,"extra":'+'['*1100+'0'+']'*1100+'}\n',
               '{"pid":1,"extra":'+('{"a":'*400)+'0'+'}'*400+'}\n']:
    f.write_text(raw);self.assertFalse(m.collect(p,1)['valid'])
   for extra in ['{"nested":1}','[1]','1e999','"\\ud800"']:
    f.write_text(valid.replace('"pid": 1', '"extra":'+extra+',"pid": 1',1))
    self.assertFalse(m.collect(p,1)['valid'])
 def test_optional_known_fields_have_exact_scalar_types(self):
  for field,value in [('child_pid',True),('supported',1),('result',-(1<<63)-1),('parent_segment',False)]:
   rows={1:[start(1),end(1)]};rows[1][-1][field]=value
   with self.assertRaises(m.Invalid):m.aggregate(rows,1)
 def test_rep_bytes_require_a_rep_entry(self):
  rows={1:[start(1),end(1)]};rows[1][0]['rep_bytes_enabled']=True
  rows[1][-1]['rep_bytes']=1
  with self.assertRaises(m.Invalid):m.aggregate(rows,1)

 def test_numeric_lexemes_match_native_scalar_contract(self):
  cases=[('extra','1.7976931348623158e308',True),
         ('extra','1.7976931348623157e308',True),
         ('extra','0e999999999999999999999',True),
         ('extra','1e-999999999999999999999',True),
         ('extra',str(int(float.fromhex('0x1.fffffffffffffp+1023'))),True),
         ('extra','9'*500,False),('extra','-'+'9'*500,False),
         ('extra','9'*100,True),('extra','-'+'9'*100,True),
         ('extra','18446744073709551615',True),
         ('extra','18446744073709551616',True),
         ('extra','-9223372036854775808',True),
         ('extra','-9223372036854775809',True),('extra','-0',True),
         ('parent_pid','-0',False),('parent_pid','0',True),
         ('parent_pid','0.0',False),('parent_pid','0e0',False),
         ('result','-0',False),('result','0',True),
         ('result','9223372036854775807',True),
         ('result','9223372036854775808',False),
         ('result','-9223372036854775808',True),
         ('result','-9223372036854775809',False)]
  with tempfile.TemporaryDirectory() as d:
   directory=Path(d);path=directory/'process-1.jsonl'
   for field,token,expected in cases:
    with self.subTest(field=field,token=token[:30]):
     initial=start(1);initial.pop(field,None)
     first=json.dumps(initial,separators=(',',':'))
     first=first[:-1]+','+json.dumps(field)+':'+token+'}'
     path.write_text(first+'\n'+json.dumps(end(1))+'\n')
     self.assertEqual(m.collect(directory,1)['valid'],expected)

def rep_breakdown(movs_bytes=0, stos_bytes=0, movs_entries=0, stos_entries=0):
    return dict(version=1, movs_bytes=movs_bytes, stos_bytes=stos_bytes,
                movs_entries=movs_entries, stos_entries=stos_entries)


def raw_rep_breakdown(value):
    return {'rep_' + field: value[field] for field in m.REP_BREAKDOWN_FIELDS}


def advertise_breakdown(streams, enabled=True):
    streams = copy.deepcopy(streams)
    for records in streams.values():
        for row in records:
            if row['event'] == 'start':
                row['rep_bytes_enabled'] = enabled
                row['rep_breakdown_version'] = 1
            elif row['event'] in ('end', 'exec_attempt'):
                row.update(raw_rep_breakdown(rep_breakdown()))
    return streams


def set_rep_breakdown(row, value):
    row.update(raw_rep_breakdown(value))
    row['rep_bytes'] = value['movs_bytes'] + value['stos_bytes']
    row['rep_entries'] = value['movs_entries'] + value['stos_entries']


class REPBreakdownTests(unittest.TestCase):
    def test_legacy_reports_do_not_infer_breakdown(self):
        rows = {1: [start(1), end(1)]}
        rows[1][0]['rep_bytes_enabled'] = True
        rows[1][-1].update(rep_bytes=64, rep_entries=2)
        result = m.aggregate(rows, 1)
        self.assertEqual(result['rep_bytes'], 64)
        for summary in [result, *result['processes'], *result['segments']]:
            self.assertNotIn('rep_breakdown', summary)

    def test_new_wire_report_emits_only_advertised_breakdown(self):
        streams = advertise_breakdown({1: [start(1), end(1)]})
        expected = rep_breakdown(24, 8, 3, 1)
        set_rep_breakdown(streams[1][-1], expected)
        for row in streams[1]:
            self.assertNotIn('rep_breakdown', row)
            self.assertTrue(all(value is None or type(value) in (bool, int, str) for value in row.values()))
        encoded = ''.join(json.dumps(row) + '\n' for row in streams[1]).encode()
        result = m.aggregate({1: m.parse_records(1, encoded)}, 1)
        self.assertEqual(result['rep_bytes'], 32)
        self.assertEqual(result['rep_entries'], 4)
        self.assertEqual(result['instructions'], 100)
        for summary in [result, *result['processes'], *result['segments']]:
            self.assertEqual(summary['rep_breakdown'], expected)

    def test_fork_and_successful_exec_sum_each_kind_once(self):
        streams = advertise_breakdown({
            1: [start(1), event(1, 1, 2, 'spawn', child_pid=2, syscall=57, supported=True),
                end(1, seq=3, kind='exec_attempt', count=100), start(1, 2), end(1, 2, count=25)],
            2: [start(2, origin='fork', parent=1, parent_segment='1-1'), end(2, count=30)],
        })
        set_rep_breakdown(streams[1][2], rep_breakdown(8, 0, 2, 0))
        set_rep_breakdown(streams[1][-1], rep_breakdown(0, 12, 0, 3))
        set_rep_breakdown(streams[2][-1], rep_breakdown(4, 0, 1, 1))
        streams[2][-1]['unsupported_rep_entries'] = 3
        result = m.aggregate(streams, 1)
        self.assertEqual(result['instructions'], 155)
        self.assertEqual(result['rep_breakdown'], rep_breakdown(12, 12, 3, 4))
        self.assertEqual((result['rep_bytes'], result['rep_entries'], result['unsupported_rep_entries']), (24, 7, 3))
        self.assertEqual(result['processes'][0]['rep_breakdown'], rep_breakdown(8, 12, 2, 3))
        self.assertEqual(result['processes'][1]['rep_breakdown'], rep_breakdown(4, 0, 1, 1))
        self.assertEqual([row['rep_breakdown'] for row in result['segments']],
                         [rep_breakdown(8, 0, 2, 0), rep_breakdown(0, 12, 0, 3), rep_breakdown(4, 0, 1, 1)])
        self.assertEqual(result['rep_telemetry_status'], 'partial')

    def test_failed_exec_checkpoint_is_not_selected_or_double_counted(self):
        for missing in ('none', 'one', 'all'):
            with self.subTest(missing=missing):
                streams = advertise_breakdown({1: [
                    start(1), end(1, kind='exec_attempt', count=100),
                    event(1, 1, 3, 'exec_failed', syscall=59, result=-2),
                    end(1, seq=4, count=125),
                ]})
                checkpoint = streams[1][1]
                # Type-correct, semantically invalid failed checkpoints have
                # always been ignored. Only the selected final image counts.
                checkpoint['valid'] = False
                checkpoint.update(raw_rep_breakdown(rep_breakdown(9, 0, 0, 0)))
                if missing == 'one':
                    del checkpoint['rep_movs_bytes']
                elif missing == 'all':
                    for field in m.REP_BREAKDOWN_RAW_FIELDS:
                        del checkpoint[field]
                set_rep_breakdown(streams[1][-1], rep_breakdown(32, 0, 2, 0))
                result = m.aggregate(streams, 1)
                self.assertEqual(result['instructions'], 125)
                self.assertEqual(result['rep_breakdown'], rep_breakdown(32, 0, 2, 0))
                self.assertEqual(len(result['segments']), 1)

    def test_successful_exec_requires_valid_selected_checkpoint(self):
        for missing in (False, True):
            streams = advertise_breakdown({1: [
                start(1), end(1, kind='exec_attempt'), start(1, 2), end(1, 2),
            ]})
            if missing:
                del streams[1][1]['rep_movs_bytes']
            else:
                streams[1][1]['rep_movs_bytes'] = 1
            with self.subTest(missing=missing), self.assertRaises(m.Invalid):
                m.aggregate(streams, 1)

    def test_zero_rcx_entries_and_rep_disabled_zeros(self):
        for enabled, expected in [(True, rep_breakdown(0, 0, 1, 4)), (False, rep_breakdown())]:
            streams = advertise_breakdown({1: [start(1), end(1)]}, enabled)
            set_rep_breakdown(streams[1][-1], expected)
            result = m.aggregate(streams, 1)
            self.assertEqual(result['rep_breakdown'], expected)
            self.assertEqual(result['rep_bytes'], 0)
            self.assertEqual(result['rep_entries'], expected['movs_entries'] + expected['stos_entries'])
        streams = advertise_breakdown({1: [start(1), end(1)]}, False)
        set_rep_breakdown(streams[1][-1], rep_breakdown(0, 0, 1, 0))
        with self.assertRaises(m.Invalid):
            m.aggregate(streams, 1)

    def test_unsupported_entries_are_not_in_the_movs_stos_partition(self):
        streams = advertise_breakdown({1: [start(1), end(1)]})
        streams[1][-1]['unsupported_rep_entries'] = 7
        result = m.aggregate(streams, 1)
        self.assertEqual(result['rep_breakdown'], rep_breakdown())
        self.assertEqual(result['rep_entries'], 0)
        self.assertEqual(result['unsupported_rep_entries'], 7)
        self.assertEqual(result['rep_telemetry_status'], 'partial')

    def test_missing_or_unsolicited_selected_breakdown_fails(self):
        for field in m.REP_BREAKDOWN_RAW_FIELDS:
            new = advertise_breakdown({1: [start(1), end(1)]})
            del new[1][-1][field]
            legacy = {1: [start(1), end(1)]}
            legacy[1][-1][field] = 0
            for streams in (new, legacy):
                with self.subTest(field=field, advertised='rep_breakdown_version' in streams[1][0]):
                    with self.assertRaises(m.Invalid):
                        m.aggregate(streams, 1)

    def test_mixed_capability_across_processes_and_exec_images_fails(self):
        trees = [fork_tree(), {1: [start(1), end(1, kind='exec_attempt'), start(1, 2), end(1, 2)]}]
        for tree in trees:
            for first_legacy in (False, True):
                streams = advertise_breakdown(tree)
                starts = [row for rows in streams.values() for row in rows if row['event'] == 'start']
                del starts[0 if first_legacy else 1]['rep_breakdown_version']
                with self.subTest(processes=len(tree), first_legacy=first_legacy), self.assertRaises(m.Invalid):
                    m.aggregate(streams, 1)

    def test_capability_version_has_exact_type(self):
        for version in (None, False, True, 0, 2, -1, 1.0, '1'):
            streams = advertise_breakdown({1: [start(1), end(1)]})
            streams[1][0]['rep_breakdown_version'] = version
            with self.subTest(version=version), self.assertRaises(m.Invalid):
                m.aggregate(streams, 1)

    def test_flat_breakdown_has_u64_counters_and_rejects_nested_objects(self):
        for value in (rep_breakdown(), [], {'version': 1}):
            streams = advertise_breakdown({1: [start(1), end(1)]})
            streams[1][-1]['rep_breakdown'] = value
            with self.subTest(object=value), self.assertRaises(m.Invalid):
                m.aggregate(streams, 1)
        for field in m.REP_BREAKDOWN_RAW_FIELDS:
            for value in (None, False, True, -1, 1.0, '0', m.U64_MAX + 1):
                streams = advertise_breakdown({1: [start(1), end(1)]})
                streams[1][-1][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(m.Invalid):
                    m.aggregate(streams, 1)

    def test_kind_entries_and_partition_are_checked(self):
        changes = [
            dict(rep_bytes=1, rep_entries=1, rep_breakdown=rep_breakdown(1, 0, 0, 1)),
            dict(rep_bytes=1, rep_entries=1, rep_breakdown=rep_breakdown(0, 1, 1, 0)),
            dict(rep_bytes=2, rep_entries=1, rep_breakdown=rep_breakdown(1, 0, 1, 0)),
            dict(rep_bytes=0, rep_entries=2, rep_breakdown=rep_breakdown(0, 0, 1, 0)),
            dict(rep_bytes=0, rep_entries=2, rep_breakdown=rep_breakdown(m.U64_MAX, 1, 1, 1)),
            dict(rep_bytes=0, rep_entries=0, rep_breakdown=rep_breakdown(0, 0, m.U64_MAX, 1)),
        ]
        for change in changes:
            streams = advertise_breakdown({1: [start(1), end(1)]})
            streams[1][-1].update(raw_rep_breakdown(change['rep_breakdown']),
                                  rep_bytes=change['rep_bytes'], rep_entries=change['rep_entries'])
            with self.subTest(change=change), self.assertRaises(m.Invalid):
                m.aggregate(streams, 1)

    def test_tree_and_exec_aggregate_overflow_fails(self):
        for unit in ('bytes', 'entries'):
            for forked in (False, True):
                tree = fork_tree() if forked else {1: [start(1), end(1, kind='exec_attempt'), start(1, 2), end(1, 2)]}
                streams = advertise_breakdown(tree)
                selected = ([streams[1][-1], streams[2][-1]] if forked else [streams[1][1], streams[1][-1]])
                for row, count in zip(selected, (m.U64_MAX, 1)):
                    value = rep_breakdown(count if unit == 'bytes' else 0, 0,
                                          1 if unit == 'bytes' else count, 0)
                    set_rep_breakdown(row, value)
                with self.subTest(unit=unit, forked=forked), self.assertRaises(m.Invalid):
                    m.aggregate(streams, 1)

    def test_flat_wire_grammar_and_invalid_output_omit_breakdown(self):
        streams = advertise_breakdown({1: [start(1), end(1)]})
        valid = ''.join(json.dumps(row) + '\n' for row in streams[1])
        invalid = [
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": true'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": -0'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": 0e0'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": 0, "rep_movs_bytes": 0'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": {"nested": 0}'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": [0]'),
            valid.replace('"rep_movs_bytes": 0', '"rep_movs_bytes": 0, "rep_breakdown": ' + json.dumps(rep_breakdown())),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'process-1.jsonl'
            path.write_text(valid)
            self.assertEqual(m.collect(Path(directory), 1)['rep_breakdown'], rep_breakdown())
            for text in invalid:
                path.write_text(text)
                result = m.collect(Path(directory), 1)
                self.assertFalse(result['valid'])
                self.assertFalse(result['complete'])
                self.assertNotIn('rep_breakdown', result)
                self.assertNotIn('instructions', result)

    def test_failed_checkpoint_still_requires_well_typed_present_fields(self):
        streams = advertise_breakdown({1: [
            start(1), end(1, kind='exec_attempt'), event(1, 1, 3, 'exec_failed', syscall=59, result=-2),
            end(1, seq=4),
        ]})
        streams[1][1]['rep_movs_bytes'] = True
        with self.assertRaises(m.Invalid):
            m.aggregate(streams, 1)


if __name__=='__main__':unittest.main()
