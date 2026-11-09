#!/usr/bin/env python3
"""Native process/image lifecycle, exact-work and bounded-invalidity smokes."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from fixtures import LOOP, REP, JIT, THREADS

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('collector',ROOT/'collect.py')
collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)


def main():
 p=argparse.ArgumentParser();p.add_argument('--sdk',type=Path,required=True)
 p.add_argument('--client',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
 p.add_argument('--pypy',default=shutil.which('pypy3'));p.add_argument('--compact-vm',action='store_true');args=p.parse_args()
 args.sdk=args.sdk.resolve();args.client=args.client.resolve();args.output_dir.mkdir(parents=True,exist_ok=True)
 rows=[]
 with tempfile.TemporaryDirectory(prefix='workmeter-smoke-',dir=args.output_dir) as directory:
  tmp=Path(directory)
  def compile(name,source,flags=()):
   f=tmp/(name+('.S' if '-nostdlib' in flags else '.c'));f.write_text(source);out=tmp/name
   r=subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',*flags,str(f),'-o',str(out)],capture_output=True,text=True)
   if r.returncode:raise RuntimeError(r.stderr)
   return out
  def run(name,argv,rep=False,valid=True,code=0,expected=None,processes=None,images=None):
   out=tmp/(name+'-records');out.mkdir()
   cmd=[str(args.sdk/'bin64/drrun'),'-quiet','-follow_children',
        *(['-vm_size','32M','-vmheap_size','32M'] if args.compact_vm else []),'-c',str(args.client),'-outdir',str(out.resolve()),
        *(['-rep_bytes'] if rep else []),'--',*map(str,argv)]
   started=time.monotonic()
   proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
   try:stdout,stderr=proc.communicate(timeout=30)
   except BaseException:
    os.killpg(proc.pid,signal.SIGKILL);proc.communicate(timeout=5);raise
   result=collector.collect(out,proc.pid)
   row=dict(test=name,exit_code=proc.returncode,elapsed_s=time.monotonic()-started,stdout=stdout.decode(),
            stderr=stderr.decode(),result=result,expected_valid=valid)
   rows.append(row)
   assert proc.returncode==code,row
   assert result['valid'] is valid,row
   if expected is not None:assert result['instructions']==expected,row
   if processes is not None:assert result['process_count']==processes,row
   if images is not None:assert result['image_count']==images,row
   return row
  asm=['-static','-nostdlib','-no-pie']
  loop=compile('loop',LOOP,asm)
  for rep in [False,True]:
   mode='rep' if rep else 'count'
   for i in range(3):run(f'{mode}-exact-loop-{i}',[loop],rep=rep,expected=300005,processes=1,images=1)
   for op in ['movs','stos']:
    for suffix,width in [('b',1),('w',2),('l',4),('q',8)]:
     for n in [0,1,8192]:
      name=f'{mode}-{op}{suffix}-{n}';program=compile(name,REP.replace('rep movsb',f'rep {op}{suffix}'),[*asm,f'-DREP_COUNT={n}'])
      r=run(name,[program],rep=rep,expected=7)['result'];assert r['rep_bytes']==(n*width if rep else 0),r
   for n in [0,32]:
    name=f'{mode}-jit-{n}';program=compile(name,JIT,[*asm,f'-DNOP_COUNT={n}'])
    run(name,[program],rep=rep,expected=5017+1000*n)
   threads=compile(mode+'-threads',THREADS,['-pthread']);r=run(mode+'-threads',[threads],rep=rep)['result']
   assert r['segments'][0]['threads']==5,r
  source=(ROOT/'tests/tree_fixture.c').read_text()
  tree=compile('tree',source)
  for mode,np,ni in [('fork',2,2),('nested',3,3),('fork-exec',2,3),('exec',1,2),('exec-chain',1,3),('failed-exec',1,1),('exec-empty-env',1,2),('vfork',2,3),('posix-spawn',2,3),('vfork-exit',2,2),('vfork-failed-exec',2,2)]:
   for rep in [False,True]:
    r=run(mode+('-rep' if rep else '-count'),[tree,mode],rep=rep,processes=np,images=ni)['result']
    assert r['instructions']==sum(p['instructions'] for p in r['processes'])
    assert r['instructions']==sum(s['instructions'] for s in r['segments'])
  # Parent prefix must not be charged again in a forked child.
  more=compile('tree-more-prefix',source,['-DPRE_WORK=20000'])
  a=run('fork-prefix-a',[tree,'fork'])['result'];b=run('fork-prefix-b',[more,'fork'])['result']
  child=lambda r:next(p['instructions'] for p in r['processes'] if p['pid']!=r['root_pid'])
  assert child(a)==child(b),(a,b)
  larger=compile('tree-more-child',source,['-DCHILD_WORK=100000'])
  c=run('fork-child-scale',[larger,'fork'])['result']
  assert child(c)-child(a)==3*50000,(a,c)
  r=run('handled-signal',[tree,'signal'])['result'];assert r['quality']=='signals_observed',r
  run('nonzero-exit',[tree,'nonzero'],code=7)
  run('killed-child',[tree,'kill-child'],valid=False)
  spawn_threads=compile('spawn-threads',(ROOT/'tests/spawn_threads.c').read_text(),['-pthread'])
  for rep in [False,True]:
   r=run('spawn-threads-'+str(rep),[spawn_threads],rep=rep,processes=7,images=13)['result']
   parent=next(s for s in r['segments'] if s['pid']==r['root_pid'])
   assert parent['threads']==4,r
  r=run('cpython',[sys.executable,'-c','print(sum(range(100000)))'])
  assert r['stdout'].strip()=='4999950000',r
  if args.pypy:
   r=run('pypy',[args.pypy,'-c','print(sum(range(100000)))']);assert r['stdout'].strip()=='4999950000',r
  else:rows.append({'test':'pypy','skipped':'runtime unavailable; not qualified'})
 result={'exit_code':0,'compact_vm':args.compact_vm,'passed':sum('result' in r for r in rows),'skipped':[r for r in rows if 'skipped' in r],
         'unsupported_coverage':[r['test'] for r in rows if r.get('coverage_supported') is False], 'rows':rows}
 (args.output_dir/'results.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':main()
