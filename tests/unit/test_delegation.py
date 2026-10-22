import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock,patch

from nativepeers.caller import Caller
from nativepeers.delegation import Delegation
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worktrees import git,prepare,snapshot


class DelegationTests(unittest.IsolatedAsyncioTestCase):
    async def test_writing_batch_rejects_tracked_and_selected_untracked_mutation(self):
        for selected in (False, True):
            with self.subTest(untracked=selected):
                await self.check_mutation(selected)

    async def check_mutation(self,selected):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=root/'home';repo=root/'repo';repo.mkdir()
            (home/'.codex').mkdir(parents=True);(home/'.codex/config.toml').write_text('')
            executable=home/'.local/bin/claude';executable.parent.mkdir(parents=True);executable.write_text('fixture only')
            git(repo,'init','-q');source=repo/'fixture.txt';source.write_text('V1\n');git(repo,'add','fixture.txt');git(repo,'commit','-qm','Create fixture baseline')
            if selected:source=repo/'selected.txt';source.write_text('V1\n')
            journal=Journal(root/'state');self.addCleanup(journal.close);registry=Registry(journal)
            parent=registry.register('codex',home,new_id(),'parent',identity(os.getpid()).json(),repo,{'kind':'fake'},owner_kind='test',policy={'profiles':['workspace']})
            caller=Caller('peer',parent['handle'],parent['incarnation_id']);delegation=Delegation(journal,registry)
            count=0
            def changing_prepare(*args,**kwargs):
                nonlocal count
                value=prepare(*args,**kwargs);count+=1
                if count==1:source.write_text('V2\n')
                return value
            request={'workers':[{'provider':'claude','title':str(i),'task':'fixture','permission_profile':'workspace','workspace':{'mode':'isolated-worktree','untracked_allowlist':['selected.txt'] if selected else []}} for i in range(2)]}
            with patch('nativepeers.delegation.Path.home',return_value=home),patch('nativepeers.worktrees.ROOT',root/'project'),patch('nativepeers.delegation.prepare',side_effect=changing_prepare),patch.object(delegation,'ensure_supervisor',new_callable=AsyncMock) as start:
                with self.assertRaises(PeerError) as error:await delegation.spawn(caller,request)
                self.assertEqual(error.exception.code,'WORKSPACE_SNAPSHOT_REQUIRED');start.assert_not_awaited()
            self.assertEqual(source.read_text(),'V2\n')
            self.assertTrue(all(row['state']=='FAILED' for row in journal.all('SELECT state FROM workers')))
            self.assertTrue(all(row['state']=='CANCELED' for row in journal.all('SELECT state FROM tasks')))

