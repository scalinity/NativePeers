"""Bounded, inference-free usage/export read benchmark in a temporary journal."""
import hashlib,json,os,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path.cwd()))
from nativepeers.artifacts import Artifacts
from nativepeers.journal import Journal
from nativepeers.registry import Registry
from nativepeers.process import identity
from nativepeers.caller import Caller
from nativepeers.models import new_id,ARTIFACT_MAX
from nativepeers.usage import store
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage
with tempfile.TemporaryDirectory() as directory:
 root=Path(directory).resolve();j=Journal(root/'state');peer=Registry(j).register('codex',root,new_id(),'fixture',identity(os.getpid()).json(),root,{},owner_kind='test')
 caller=Caller('peer',peer['handle'],peer['incarnation_id']);a=Artifacts(j);handle=a.put(b'x'*ARTIFACT_MAX,caller.key)
 count=0;size=0;source_bytes=0;original=hashlib.file_digest;original_read=os.read
 source_stat=(j.root/'artifacts'/handle).stat()
 def measured_read(fd,limit):
  global source_bytes
  result=original_read(fd,limit);info=os.fstat(fd)
  if (info.st_dev,info.st_ino)==(source_stat.st_dev,source_stat.st_ino):source_bytes+=len(result)
  return result
 def measured(stream,*args,**kwargs):
  global count,size
  count+=1;size+=os.fstat(stream.fileno()).st_size
  return original(stream,*args,**kwargs)
 began=time.perf_counter()
 with patch('hashlib.file_digest',side_effect=measured),patch('os.read',side_effect=measured_read):a.export(handle,root/'out',caller)
 export=dict(seconds=time.perf_counter()-began,full_digest_passes=count,bytes_hashed=size,artifact_bytes=ARTIFACT_MAX,streamed_source_bytes=source_bytes)
 now=time.time()
 for i in range(32):store.record(j,'codex',root,dict(source='fixture-native',models=[{'model':str(i)} for i in range(64)],pools=[pool(str(p),used=10+i,reset=now+1000,duration=18000,resolution=1,models=['*']) for p in range(8)]),at=now-310+i*10)
 queries=[];j.db.set_trace_callback(queries.append);began=time.perf_counter()
 for _ in range(5):Usage(j).snapshot('codex',root)
 usage=dict(seconds=time.perf_counter()-began,reads=5,sql_statements=len(queries))
 print(json.dumps(dict(export=export,usage=usage),indent=2));j.close()
