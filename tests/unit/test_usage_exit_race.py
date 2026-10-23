"""Later inspection uses real process exit, without an inference or a fake /proc error.

The Linux cases also run with stdlib unittest in a disposable Linux container.
"""
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.process import identity,owned_status,signal_captured
from nativepeers.usage import store
from nativepeers.usage.native_command import run_native
from nativepeers.usage.service import Usage


class UsageExitRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.binary=self.root/'native'
        self.j=Journal(self.root/'state');self.addCleanup(self.j.close)

    async def collect(self):
        return await run_native('antigravity',self.binary,[],self.root,timeout=5)

    async def test_permission_failure_after_capture_is_inspection_uncertainty(self):
        self.binary.write_text('#!'+sys.executable+'\nimport time\ntime.sleep(30)\n');self.binary.chmod(0o700)
        births=[]
        def inspect(pid):
            if births:raise PermissionError('fixture denied later inspection')
            result=identity(pid);births.append(result.json());return result
        with patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]),patch('nativepeers.usage.native_command.identity',side_effect=inspect):
            with self.assertRaises(PeerError) as failure:await self.collect()
        self.assertEqual(failure.exception.code,'OWNERSHIP_UNKNOWN')
        self.assertTrue(self.binary.exists());self.assertEqual(owned_status(births[0]),'DEAD')

    async def test_unrelated_missing_source_file_does_not_claim_missing_executable(self):
        self.binary.write_text('present fixture')
        async def collector(*args):raise FileNotFoundError('missing source capture')
        with patch('nativepeers.usage.service.COLLECTORS',{'antigravity':type('C',(),{'collect':staticmethod(collector)})()}):
            await Usage(self.j).refresh('antigravity',self.root,force=True)
        self.assertEqual(store.latest(self.j,'antigravity',self.root,'refresh')['payload']['code'],'SOURCE_UNAVAILABLE')

    async def test_missing_executable_keeps_its_specific_classification(self):
        from nativepeers.usage.collectors import NativeCollector
        with patch('nativepeers.usage.collectors.executable',return_value=self.binary):
            with self.assertRaises(PeerError) as failure:await NativeCollector('antigravity').collect(self.j,self.root)
        self.assertEqual(failure.exception.code,'EXECUTABLE_NOT_FOUND')

    async def linux_race(self,target,*,public=False,inherited=False):
        gate=self.root/'finish';child_file=self.root/'child';fired=[];births={};calls={};processes=[]
        child_code='import pathlib,time; '+('time.sleep(30)' if inherited else f'p=pathlib.Path({str(gate)!r})\nwhile not p.exists():time.sleep(.005)')
        code='import pathlib,subprocess,sys,time\n'
        if target=='child' or inherited:
            code+=f'p=subprocess.Popen([sys.executable,"-c",{child_code!r}],start_new_session=True)\npathlib.Path({str(child_file)!r}).write_text(str(p.pid))\n'
        if target=='child':code+='p.wait()\n'
        else:code+=f'gate=pathlib.Path({str(gate)!r})\nwhile not gate.exists():time.sleep(.005)\n'
        code+='print(\'{"ok":true}\',flush=True)\n'
        self.binary.write_text('#!'+sys.executable+'\n'+code);self.binary.chmod(0o700)
        actual_popen=subprocess.Popen;actual_resolve=Path.resolve
        def create(*args,**kwargs):
            process=actual_popen(*args,**kwargs)
            if isinstance(args[0],list) and args[0][0]==str(self.binary):processes.append(process)
            return process
        def inspect(pid):
            calls[pid]=calls.get(pid,0)+1
            child=int(child_file.read_text()) if child_file.exists() else None
            selected=bool(processes) and pid==(child if target=='child' else processes[0].pid)
            armed=selected and calls[pid]>=2 and not fired and (not inherited or child in births)
            def resolve(path,*args,**kwargs):
                if armed and not fired and str(path)==f'/proc/{pid}/exe':
                    # identity() has already read the real stat, UID and boot ID.
                    fired.append(pid);gate.touch()
                    deadline=time.monotonic()+2
                    while Path(f'/proc/{pid}').exists():
                        try:state=Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()[0]
                        except FileNotFoundError:break
                        if state=='Z':break
                        if time.monotonic()>deadline:raise AssertionError('Fixture did not exit at the inspection gate')
                        time.sleep(.005)
                return actual_resolve(path,*args,**kwargs)
            with patch.object(Path,'resolve',new=resolve):result=identity(pid)
            births[pid]=result.json();return result
        async def collector(*args):
            result=await self.collect()
            self.assertTrue(json.loads(result['stdout'])['ok']);self.assertEqual(result['cleanup']['state'],'CLOSED')
            return {'source':'native-fixture','pools':[],'models':[]}
        try:
            with patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]),patch('nativepeers.usage.native_command.identity',side_effect=inspect),patch('nativepeers.usage.native_command.subprocess.Popen',side_effect=create):
                if public:
                    with patch('nativepeers.usage.service.COLLECTORS',{'antigravity':type('C',(),{'collect':staticmethod(collector)})()}):
                        await Usage(self.j).refresh('antigravity',self.root,force=True)
                    self.assertIsNotNone(store.latest(self.j,'antigravity',self.root,'snapshot',active=True))
                    self.assertIsNone(store.latest(self.j,'antigravity',self.root,'refresh'))
                elif inherited:
                    with self.assertRaises(PeerError) as failure:await self.collect()
                    self.assertEqual(failure.exception.code,'SOURCE_TIMEOUT')
                    self.assertEqual(failure.exception.details['cleanup'],'CLOSED')
                else:await collector()
            self.assertTrue(fired);self.assertTrue(self.binary.exists())
            self.assertTrue(births);self.assertTrue(all(owned_status(r)=='DEAD' for r in births.values()))
        finally:
            gate.touch()
            for record in births.values():signal_captured(record,signal.SIGKILL)
            for process in processes:
                if process.poll() is None:process.kill()
                process.wait(timeout=2)

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux /proc exit-between-stat-and-exe schedule')
    async def test_later_leader_exit_keeps_completed_output(self):await self.linux_race('leader')

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux /proc exit-between-stat-and-exe schedule')
    async def test_later_descendant_exit_keeps_completed_output(self):await self.linux_race('child')

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux /proc exit-between-stat-and-exe schedule')
    async def test_public_refresh_preserves_success_after_later_leader_exit(self):await self.linux_race('leader',public=True)

    @unittest.skipUnless(sys.platform.startswith('linux'),'Linux /proc exit-between-stat-and-exe schedule')
    async def test_later_leader_exit_does_not_erase_inherited_pipe_deadline(self):await self.linux_race('leader',inherited=True)


if __name__=='__main__':unittest.main()
