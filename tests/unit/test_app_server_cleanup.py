"""Real inherited output pipes cannot hold metadata/worker teardown indefinitely."""
import asyncio,json,os,signal,sys,tempfile,time
from pathlib import Path
from unittest.mock import patch
import pytest,unittest
from nativepeers.adapters.codex_app_server import AppServer
from nativepeers.errors import PeerError
from nativepeers.process import identity,signal_owned,owned_status,signal_captured

class AppServerCleanupTests(unittest.IsolatedAsyncioTestCase):
 async def test_exited_leader_with_inherited_pipes_reports_bounded_uncertainty(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);pidfile=root/'child';script=root/'server.py';ready=root/'child-ready'
   script.write_text('import sys,json,subprocess,pathlib\n'
     'for line in sys.stdin:\n value=json.loads(line)\n'
     ' if value.get("method")=="finish":\n'
     f'  child=subprocess.Popen([sys.executable,"-c",{("import pathlib,time;pathlib.Path("+repr(str(ready))+").touch();time.sleep(30)")!r}],start_new_session=True)\n'
     f'  pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n'
     ' if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\n'
     ' if value.get("method")=="finish":break\n')
   server=AppServer(Path(sys.executable),[str(script)]);child=None
   capture_allowed=asyncio.Event();capture_waiting=asyncio.Event();capture=server._capture
   async def delayed_capture():
    capture_waiting.set();await capture_allowed.wait();await capture()
   try:
    # Block ancestry capture until the parent is positively dead. A ready file
    # alone cannot establish this schedule: the ordinary monitor may win first.
    with patch.object(server,'_capture',new=delayed_capture):
     await asyncio.wait_for(server.start(),3)
     await asyncio.wait_for(capture_waiting.wait(),3)
     await server.call('finish',{},timeout=3)
     async with asyncio.timeout(3):
      while not ready.exists() or server.process.returncode is None or owned_status(server.owner)!='DEAD':await asyncio.sleep(.005)
     child=identity(int(pidfile.read_text())).json()
     assert not server.descendants and owned_status(child)=='LIVE'
     capture_allowed.set();await capture()
     assert not server.descendants
     started=time.monotonic()
     with pytest.raises(PeerError) as error:await asyncio.wait_for(server.close(exit_timeout=.3,kill_timeout=.2,reader_timeout=.1),2)
     assert time.monotonic()-started<2 and error.value.code=='CLEANUP_UNCERTAIN'
     assert server.process.returncode==0 and server.cleanup['pending_output_readers']==2
     assert not server.stdout_eof and not server.stderr_eof
     assert server.reader.done() and server.error_reader.done() and owned_status(child)=='LIVE'
     with pytest.raises(PeerError):await server.close()
   finally:
    capture_allowed.set()
    if child:signal_captured(child,signal.SIGKILL)
    if server.process and server.process.returncode is None:server.process.kill()
    if server.process:
     async with asyncio.timeout(3):
      while child and owned_status(child)!='DEAD':await asyncio.sleep(.01)
     await asyncio.wait_for(server.process.wait(),3)
     if server.cleanup is None:await server.close(exit_timeout=.1,kill_timeout=.1,reader_timeout=.1)
 
 async def test_normal_close_finishes_readers_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as directory:
   script=Path(directory)/'server.py'
   script.write_text('import sys,json\nfor line in sys.stdin:\n value=json.loads(line)\n if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\n')
   server=AppServer(Path(sys.executable),[str(script)]);await asyncio.wait_for(server.start(),3)
   result=await asyncio.wait_for(server.close(),2)
   assert result['state']=='CLOSED' and server.reader.done() and server.error_reader.done()
   assert await server.close()==result

 async def test_captured_escaped_descendant_and_parser_failure_require_real_closure(self):
  from nativepeers.process import owned_status
  for malformed,close_pipes in [(False,False),(False,True),(True,False),('queue',False)]:
   with self.subTest(malformed=malformed),tempfile.TemporaryDirectory() as directory:
    root=Path(directory).resolve();script=root/'server.py';pidfile=root/'pid';ready=root/'ready'
    code='import os,pathlib,time; '+('os.close(1);os.close(2);' if close_pipes else '')+f'pathlib.Path({str(ready)!r}).touch();time.sleep(30)'
    failure_output='invalid-json' if malformed!='queue' else json.dumps({'method':'event1'})+'\n'+json.dumps({'method':'event2'})
    script.write_text('import sys,json,subprocess,pathlib\n'
     f'child=subprocess.Popen([sys.executable,"-c",{code!r}],start_new_session=True)\n'
     f'pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n'
     'for line in sys.stdin:\n value=json.loads(line)\n if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\n'
     +(f' if value.get("method")=="break":print({failure_output!r},flush=True)\n' if malformed else ''))
    server=AppServer(Path(sys.executable),[str(script)]);child=None
    try:
     await asyncio.wait_for(server.start(),3)
     async with asyncio.timeout(3):
      while not ready.exists():await asyncio.sleep(.01)
      child=identity(int(pidfile.read_text())).json()
      while child['pid'] not in server.descendants:await asyncio.sleep(.01)
     if malformed:
      if malformed=='queue':server.events=asyncio.Queue(maxsize=1)
      await server.write({'method':'break'})
      await asyncio.wait_for(server.reader,1)
      self.assertFalse(server.stdout_eof)
      self.assertEqual(owned_status(child),'LIVE')
     report=await asyncio.wait_for(server.close(exit_timeout=.1,kill_timeout=.2,reader_timeout=.2),2)
     self.assertEqual(report['state'],'CLOSED');self.assertTrue(report['output_eof'])
     self.assertTrue(report['stdout_eof']);self.assertTrue(report['stderr_eof'])
     self.assertEqual(owned_status(child),'DEAD')
     self.assertEqual(report['captured_descendants'],1)
    finally:
     if child and owned_status(child)=='LIVE':signal_owned(identity(child['pid']).json(),signal.SIGKILL)
     if server.process and server.process.returncode is None:server.process.kill()

 async def test_cancellation_during_close_retains_uncertain_ownership(self):
  from nativepeers.process import signal_captured,owned_status
  with tempfile.TemporaryDirectory() as directory:
   script=Path(directory)/'server.py'
   script.write_text('import sys,json,time\nfor line in sys.stdin:\n value=json.loads(line)\n if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\ntime.sleep(30)\n')
   server=AppServer(Path(sys.executable),[str(script)])
   try:
    await asyncio.wait_for(server.start(),3)
    closing=asyncio.create_task(server.close(exit_timeout=10));await asyncio.sleep(.03);closing.cancel()
    with pytest.raises(asyncio.CancelledError):await closing
    self.assertEqual(server.cleanup['state'],'UNCERTAIN')
    self.assertFalse(server.cleanup['output_eof'])
    self.assertEqual(owned_status(server.owner),'LIVE')
   finally:
    if server.owner:signal_captured(server.owner,signal.SIGKILL)
    await asyncio.gather(server.reader,server.error_reader,return_exceptions=True)
    await asyncio.wait_for(server.process.wait(),3)

 async def test_initial_inspection_failure_still_closes_created_handle_and_readers(self):
  from unittest.mock import patch
  from nativepeers.process import identity as actual_identity
  with tempfile.TemporaryDirectory() as directory:
   script=Path(directory)/'server.py';received=Path(directory)/'input'
   script.write_text('import sys,pathlib\npathlib.Path('+repr(str(received))+').write_text(sys.stdin.read())\n')
   server=AppServer(Path(sys.executable),[str(script)]);calls=0
   def inspect(pid):
    nonlocal calls
    calls+=1
    if calls==1:raise PermissionError('fixture first inspection')
    return actual_identity(pid)
   with patch('nativepeers.adapters.codex_app_server.identity',side_effect=inspect):
    with pytest.raises(PeerError) as error:await asyncio.wait_for(server.start(),2)
   assert error.value.code in ('OWNERSHIP_UNKNOWN','CLEANUP_UNCERTAIN')
   assert server.process.returncode==0 and received.read_text()==''
   assert server.reader.done() and server.error_reader.done()
   assert server.stdout_eof and server.stderr_eof
