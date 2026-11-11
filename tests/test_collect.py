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

if __name__=='__main__':unittest.main()
