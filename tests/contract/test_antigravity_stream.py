import json
import os
import shutil
from pathlib import Path
import tempfile
import time
import sys
import unittest
from unittest.mock import patch

from nativepeers.antigravity_install import worker_profile,desired
from nativepeers.compatibility import fingerprint
from nativepeers.files import atomic_json,canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.worker_process import Worker

ROOT=Path(__file__).resolve().parents[2]


class AntigravityContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_stream_partial_lines_and_exact_history_followup(self):
        await self.run_scenario('success',followup=True)

    async def test_native_stream_negative_identity_hook_model_and_cancel_boundaries(self):
        for scenario in ['no-hook','wrong-id','display-model','CANCELED','INTERRUPTED']:
            with self.subTest(scenario=scenario):await self.run_scenario(scenario)

    async def run_scenario(self,scenario,followup=False):
        with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'HOME':directory}):
            home=Path(directory);j=Journal(home/'state');self.addCleanup(j.close)
            profile=home/'.gemini/config/agents/nativepeers-analysis.md';profile.parent.mkdir(parents=True);profile.write_text(worker_profile('analysis'))
            hooks=home/'.gemini/config/hooks.json';hooks.write_text(json.dumps({'nativepeers':desired(home)[hooks][2]}))
            group,worker,first,second=[new_id() for _ in range(4)]
            binary=home/'fake_antigravity.py';shutil.copyfile(ROOT/'tests/fixtures/fake_antigravity.py',binary);binary.chmod(0o755)
            binary.write_text(binary.read_text().replace('#!/usr/bin/env python3','#!'+sys.executable,1))
            binary.with_suffix('.scenario').write_text(scenario)
            native_binary=home/'.local/bin/agy';native_binary.parent.mkdir(parents=True);native_binary.symlink_to(identity(os.getpid()).executable)
            spec={'provider':'antigravity','model':'gemini-fixture-high','title':'fixture','cwd':directory,'permission_profile':'analysis','max_task_turns':2,'wall_time_seconds':5}
            if scenario=='display-model':spec['model_display_name']='Gemini Fixture High'
            lease={'spec':spec,'parent_key':'parent','binding':str(j.root/'bindings'/new_id()),'grants':{'group_id':group,'worker_id':worker},'expires_at':time.time()+60,'executable':str(binary)}
            j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)',(group,'parent','{}','RESERVED','{}'))
            j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',(worker,group,None,'antigravity',None,'fixture',spec['model'],None,None,directory,None,'RESERVED',1,canonical(lease).decode()))
            for task in [first,second]:j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)',(task,worker,None,None,'QUEUED',None,None,1,'{}'))
            atomic_json(j.root/'compat/runtime.json',{'providers':{'antigravity':fingerprint(binary)}})
            value=Worker(j,worker)
            try:
                await value.prepare()
                await value.task(first,'one');native=value.native
                if followup:await value.task(second,'two')
                expected='FAILED' if scenario in ('no-hook','wrong-id') else 'CANCELED' if scenario in ('CANCELED','INTERRUPTED') else 'COMPLETED'
                self.assertEqual(j.one('SELECT state FROM tasks WHERE task_id=?',(first,))['state'],expected,
                    str(j.one('SELECT terminal_reason FROM tasks WHERE task_id=?',(first,))))
                if followup:
                    self.assertEqual(value.native,native)
                    self.assertTrue(all(r['state']=='COMPLETED' for r in j.all('SELECT state FROM tasks')))
                if scenario=='display-model':self.assertEqual(value.model_observed,'gemini-fixture-high')
                if scenario!='no-hook':self.assertIsNotNone(j.one("SELECT * FROM events WHERE kind='antigravity-tool-gate'"))
                self.assertEqual(len(j.all('SELECT * FROM peer_instances')),1)
            finally:await value.close()
