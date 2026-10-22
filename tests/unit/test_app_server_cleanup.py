"""Real inherited output pipes cannot hold metadata/worker teardown indefinitely."""
import asyncio,json,os,signal,sys,tempfile,time
from pathlib import Path
import pytest,unittest
from nativepeers.adapters.codex_app_server import AppServer
from nativepeers.errors import PeerError
from nativepeers.process import identity,matches,signal_owned

class AppServerCleanupTests(unittest.IsolatedAsyncioTestCase):
 async def test_exited_leader_with_inherited_pipes_reports_bounded_uncertainty(self):
  with tempfile.TemporaryDirectory() as directory:
   root=Path(directory);pidfile=root/'child';script=root/'server.py';ready=root/'child-ready'
   script.write_text('import sys,json,subprocess,pathlib\n'
     f'child=subprocess.Popen([sys.executable,"-c",{("import pathlib,time;pathlib.Path("+repr(str(ready))+").touch();time.sleep(30)")!r}])\n'
     f'pathlib.Path({str(pidfile)!r}).write_text(str(child.pid))\n'
     'for line in sys.stdin:\n value=json.loads(line)\n if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\n')
   server=AppServer(Path(sys.executable),[str(script)]);child=None
   try:
    await asyncio.wait_for(server.start(),3)
    async with asyncio.timeout(3):
     while not ready.exists():await asyncio.sleep(.005)
    child=identity(int(pidfile.read_text())).json()
    started=time.monotonic()
    with pytest.raises(PeerError) as error:await asyncio.wait_for(server.close(exit_timeout=.3,kill_timeout=.2,reader_timeout=.1),2)
    assert time.monotonic()-started<2 and error.value.code=='CLEANUP_UNCERTAIN'
    assert server.process.returncode==0 and server.cleanup['pending_output_readers']==2
    assert server.reader.done() and server.error_reader.done() and matches(child)
    with pytest.raises(PeerError):await server.close()
   finally:
    if child and matches(child):signal_owned(child,signal.SIGTERM)
 
 async def test_normal_close_finishes_readers_and_is_idempotent(self):
  with tempfile.TemporaryDirectory() as directory:
   script=Path(directory)/'server.py'
   script.write_text('import sys,json\nfor line in sys.stdin:\n value=json.loads(line)\n if "id" in value:print(json.dumps({"id":value["id"],"result":{}}),flush=True)\n')
   server=AppServer(Path(sys.executable),[str(script)]);await asyncio.wait_for(server.start(),3)
   result=await asyncio.wait_for(server.close(),2)
   assert result['state']=='CLOSED' and server.reader.done() and server.error_reader.done()
   assert await server.close()==result
