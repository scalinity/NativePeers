from contextlib import nullcontext
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.install import Installer
from nativepeers.journal import Journal


class InstallHomeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.selected=self.root/'selected'
        self.default=self.root/'default';self.ambient=self.root/'ambient'
        self.project=self.root/'project'
        self.journal=Journal(self.root/'state');self.addCleanup(self.journal.close)
        binary=self.default/'.local/share/codex-mcp-manager/current/bin/codex'
        binary.parent.mkdir(parents=True)
        fixture=Path(__file__).parents[1]/'fixtures/fake_install_app_server.py'
        binary.write_text('#!'+sys.executable+'\n'+fixture.read_text());binary.chmod(0o755)
        for home in (self.selected/'.codex',self.default/'.codex',self.ambient):
            home.mkdir(parents=True)
            (home/'config.toml').write_text('model="retained"\n[features]\ndaemon_auto_start=false\n')
        for provider in ('claude','codex'):
            skill=self.project/'integrations'/provider/'skills/nativepeers/SKILL.md'
            skill.parent.mkdir(parents=True);skill.write_text('fixture skill\n')
        (self.project/'bin').mkdir()
        for name in ('npeer','npeer-hook'):(self.project/'bin'/name).write_text('#!/bin/sh\n')
        info={'executable':str(binary),'executable_sha256':fingerprint(binary)['sha256'],'version':'fixture'}
        atomic_json(self.project/'evidence/capabilities.json',{'providers':{'claude':info,'codex':info}})
        for item in (patch('nativepeers.install.ROOT',self.project),
                     patch('nativepeers.install.Path.home',return_value=self.default),
                     patch.dict(os.environ,HOME=str(self.default),CODEX_HOME=str(self.ambient))):
            item.start();self.addCleanup(item.stop)
        self.installer=Installer(self.journal,home=self.selected)
        lock=patch.object(self.installer,'lock',side_effect=nullcontext)
        lock.start();self.addCleanup(lock.stop)

    async def test_real_transport_install_and_uninstall_pin_selected_home(self):
        untouched={p:p.read_bytes() for home in (self.default/'.codex',self.ambient) for p in home.rglob('*') if p.is_file()}
        result=await self.installer.install()
        self.assertTrue(result['installed'])
        manifest=json.loads(self.installer.manifest.read_text())
        self.assertEqual(manifest['state'],'INSTALLED')
        self.assertEqual(set(manifest['trusted_hooks']),{'SessionStart','SessionEnd'})
        config=tomllib.loads(self.installer.config.read_text())
        self.assertEqual(config['hooks']['state']['SessionStart']['trusted_hash'],'hash-SessionStart')
        self.assertEqual(config['model'],'retained');self.assertFalse(config['features']['daemon_auto_start'])
        result=await self.installer.uninstall()
        self.assertTrue(result['uninstalled'])
        self.assertTrue(json.loads(self.installer.manifest.read_text())['uninstalled'])
        self.assertEqual(tomllib.loads(self.installer.config.read_text())['hooks']['state'],{})
        log=[json.loads(line) for line in (self.installer.config.parent/'fake-app-server.jsonl').read_text().splitlines()]
        self.assertEqual({item['codex_home'] for item in log},{str(self.installer.config.parent)})
        self.assertEqual([item['method'] for item in log].count('config/batchWrite'),2)
        self.assertEqual({p:p.read_bytes() for p in untouched},untouched)
        for home in (self.default/'.codex',self.ambient):
            self.assertEqual(set(home.iterdir()),{home/'config.toml'})

    async def test_missing_selected_user_layer_is_a_structured_error_without_config_write(self):
        for mode in ('missing','other','profile'):
            with self.subTest(mode=mode):
                self.installer.config.write_text('fixture_layer="'+mode+'"\n')
                before=self.installer.config.read_bytes()
                for remove in (False,True):
                    with self.assertRaises(PeerError) as error:
                        await self.installer.trust(remove=remove,manifest={'trusted_hooks':{'fixture':'hash'}})
                    self.assertEqual(error.exception.code,'CAPABILITY_MISMATCH')
                    self.assertEqual(error.exception.details,{'expected_user_config':str(self.installer.config)})
                    self.assertFalse(error.exception.may_have_received)
                    self.assertEqual(self.installer.config.read_bytes(),before)
        log=[json.loads(line) for line in (self.installer.config.parent/'fake-app-server.jsonl').read_text().splitlines()]
        self.assertNotIn('config/batchWrite',[item['method'] for item in log])
