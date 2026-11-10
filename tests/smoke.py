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
 p.add_argument('--pypy',default=shutil.which('pypy3'));p.add_argument('--compact-vm',action='store_true')
 p.add_argument('--collector-bin',type=Path,help='compare native Rust collector with the Python reference')
 p.add_argument('--runner-bin',type=Path,help='also qualify the standalone Rust supervisor with real instrumentation')
 args=p.parse_args()
 if args.collector_bin:args.collector_bin=args.collector_bin.resolve()
 if args.runner_bin:args.runner_bin=args.runner_bin.resolve()
 args.sdk=args.sdk.resolve();args.client=args.client.resolve();args.output_dir.mkdir(parents=True,exist_ok=True)
 rows=[]
 with tempfile.TemporaryDirectory(prefix='durante-smoke-',dir=args.output_dir) as directory:
  tmp=Path(directory)
  def compile(name,source,flags=()):
   f=tmp/(name+('.S' if '-nostdlib' in flags else '.c'));f.write_text(source);out=tmp/name
   r=subprocess.run(['gcc','-O2','-Wall','-Wextra','-Werror',*flags,str(f),'-o',str(out)],capture_output=True,text=True)
   if r.returncode:raise RuntimeError(r.stderr)
   return out
  def run(name,argv,rep=False,valid=True,code=0,expected=None,processes=None,images=None,qualified=True):
   out=tmp/(name+'-records');out.mkdir()
   cmd=[str(args.sdk/'bin64/drrun'),'-quiet','-follow_children',
        *(['-vm_size','32M','-vmheap_size','32M'] if args.compact_vm else []),'-c',str(args.client),'-outdir',str(out.resolve()),
        *(['-rep_bytes'] if rep else []),'--',*map(str,argv)]
   started=time.monotonic()
   proc=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True)
   try:stdout,stderr=proc.communicate(timeout=30 if qualified else 5)
   except subprocess.TimeoutExpired:
    os.killpg(proc.pid,signal.SIGKILL);stdout,stderr=proc.communicate(timeout=5)
    if qualified:raise
   except BaseException:
    os.killpg(proc.pid,signal.SIGKILL);proc.communicate(timeout=5);raise
   result=collector.collect(out,proc.pid)
   if args.collector_bin:
    rust=subprocess.run([str(args.collector_bin),'collect','--dir',str(out),'--root-pid',str(proc.pid)],capture_output=True,text=True,timeout=10)
    native=json.loads(rust.stdout)
    assert rust.returncode==(0 if result['valid'] else 1),(name,rust.returncode)
    if result['valid']:assert native==result,(name,'Rust/reference aggregate differs',native,result)
    else:assert native.get('valid') is False and native.get('complete') is False and 'instructions' not in native,(name,native)
   row=dict(test=name,exit_code=proc.returncode,elapsed_s=time.monotonic()-started,stdout=stdout.decode(),
            stderr=stderr.decode(),result=result,expected_valid=valid if qualified else None,coverage_supported=qualified)
   rows.append(row)
   if qualified:
    assert proc.returncode==code,row
    assert result['valid'] is valid,row
   elif proc.returncode!=0:assert result['valid'] is False,row
   if not result['valid']:
    assert result['complete'] is False and 'instructions' not in result,row
    return row
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
  # Lifecycle streams must survive normal descriptor cleanup by the target.
  # The baseline raw-open stream is closed here and cannot emit its end record.
  close_fds=compile('close-fds','#include <unistd.h>\nint main(void) { for (int fd=3; fd<256; ++fd) close(fd); return 0; }\n')
  run('close-fds',[close_fds],processes=1,images=1)
  close_range=compile('close-range','#define _GNU_SOURCE\n#include <unistd.h>\n#include <sys/syscall.h>\n#include <errno.h>\nint main(void) { long r=syscall(SYS_close_range,3u,255u,0u); return r==0 || errno==ENOSYS ? 0 : 1; }\n')
  run('close-range',[close_range],processes=1,images=1)
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
   if r['valid']:
    parent=next(s for s in r['segments'] if s['pid']==r['root_pid'])
    assert parent['threads']==4,r
  r=run('cpython',[sys.executable,'-c','print(sum(range(100000)))'])
  assert r['stdout'].strip()=='4999950000',r
  if args.pypy:
   r=run('pypy',[args.pypy,'-c','print(sum(range(100000)))']);assert r['stdout'].strip()=='4999950000',r
  else:rows.append({'test':'pypy','skipped':'runtime unavailable; not qualified'})
  if args.runner_bin:
   def run_supervised(name,argv,valid=True,code=0,expected=None,processes=None,images=None,timeout=30000,qualified=True):
    report=tmp/(name+'-supervised.json')
    cmd=[str(args.runner_bin),'run','--drrun',str(args.sdk/'bin64/drrun'),'--client',str(args.client),
         '--report',str(report),'--timeout-ms',str(timeout),'--',*map(str,argv)]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout/1000+10)
    envelope=json.loads(report.read_text());result=envelope['measurement'];execution=envelope['execution']
    row={'test':'rust-run-'+name,'exit_code':r.returncode,'stdout':r.stdout,'stderr':r.stderr,
         'execution':execution,'result':result,'expected_valid':valid if qualified else None,'coverage_supported':qualified};rows.append(row)
    assert execution['cleanup_complete'] is True,row
    assert execution['records_cleanup_complete'] is True,row
    if not qualified and r.returncode!=0:
     assert r.returncode in (1,124) and result.get('valid') is False and result.get('complete') is False and 'instructions' not in result,row
     return row
    assert r.returncode==(0 if valid else (124 if name=='timeout' else 1)),row
    assert result.get('valid') is valid and result.get('complete') is valid,row
    if name=='timeout':
     assert execution['state']=='timed_out' and 'instructions' not in result,row
    else:assert execution['exit_code']==code,row
    if expected is not None:assert result['instructions']==expected,row
    if processes is not None:assert result['process_count']==processes,row
    if images is not None:assert result['image_count']==images,row
    return row
   run_supervised('exact-loop',[loop],expected=300005,processes=1,images=1)
   run_supervised('close-fds',[close_fds],processes=1,images=1)
   run_supervised('close-range',[close_range],processes=1,images=1)
   rep_program=compile('runner-rep',REP,[*asm,'-DREP_COUNT=8192'])
   assert run_supervised('rep',[rep_program],expected=7)['result']['rep_bytes']==8192
   jit_program=compile('runner-jit',JIT,[*asm,'-DNOP_COUNT=32'])
   run_supervised('jit',[jit_program],expected=37017)
   for mode,np,ni in [('fork',2,2),('nested',3,3),('fork-exec',2,3),('exec-chain',1,3),('failed-exec',1,1),('exec-empty-env',1,2),('vfork',2,3),('posix-spawn',2,3),('vfork-exit',2,2),('vfork-failed-exec',2,2)]:
    run_supervised(mode,[tree,mode],processes=np,images=ni)
   run_supervised('spawn-threads',[spawn_threads],processes=7,images=13,timeout=3000)
   run_supervised('nonzero-exit',[tree,'nonzero'],code=7)
   assert run_supervised('handled-signal',[tree,'signal'])['result']['quality']=='signals_observed'
   run_supervised('killed-child',[tree,'kill-child'],valid=False)
   assert run_supervised('cpython',[sys.executable,'-c','print(sum(range(100000)))'])['stdout'].strip()=='4999950000'
   if args.pypy:assert run_supervised('pypy',[args.pypy,'-c','print(sum(range(100000)))'])['stdout'].strip()=='4999950000'
   forever=compile('runner-timeout','#include <unistd.h>\nint main(void) { for (;;) pause(); }\n')
   run_supervised('timeout',[forever],valid=False,timeout=300)
 result={'exit_code':0,'compact_vm':args.compact_vm,'passed':sum('result' in r and r.get('coverage_supported',True) for r in rows),'skipped':[r for r in rows if 'skipped' in r],
         'diagnostic_cases':sum(r.get('coverage_supported') is False for r in rows),
         'diagnostic_failures':[r['test'] for r in rows if r.get('coverage_supported') is False and (r['exit_code']!=0 or not r['result']['valid'])],
         'rust_collector_checked':bool(args.collector_bin),'rust_runner_checked':bool(args.runner_bin),
         'unsupported_coverage':[r['test'] for r in rows if r.get('coverage_supported') is False], 'rows':rows}
 (args.output_dir/'results.json').write_text(json.dumps(result,indent=2)+'\n')
 print(json.dumps({k:v for k,v in result.items() if k!='rows'}))


if __name__=='__main__':main()
