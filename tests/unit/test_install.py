import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from nativepeers.errors import PeerError
from nativepeers.files import private_directory
from nativepeers.install import Installer
from nativepeers.journal import Journal


class InstallTests(unittest.IsolatedAsyncioTestCase):
    async def test_concurrent_hook_edit_is_preserved_during_install_and_uninstall(self):
        for remove in (False,True):
            with self.subTest(remove=remove),tempfile.TemporaryDirectory() as directory:
                root=Path(directory);home=private_directory(root/'home');journal=Journal(root/'state')
                self.addCleanup(journal.close)
                settings=home/'.claude/settings.json';settings.parent.mkdir();settings.write_text('{"unrelated":false}')
                installer=Installer(journal,home=home,native=False)
                if remove:await installer.install()
                merged=installer.merged;newer=None
                def concurrent_merge(path,provider,remove=False):
                    nonlocal newer
                    before,after=merged(path,provider,remove)
                    if provider=='claude':
                        newer=json.dumps(dict(before,laterEdit={'enabled':False})).encode()
                        path.write_bytes(newer)
                    return before,after
                plan={'operations':[{'operation':'fixture'}],'command_collision':False}
                with patch.object(installer,'plan',return_value=plan),patch.object(installer,'merged',side_effect=concurrent_merge):
                    with self.assertRaises(PeerError) as error:
                        await (installer.uninstall() if remove else installer.install())
                self.assertEqual(error.exception.code,'OWNERSHIP_CONFLICT')
                self.assertEqual(settings.read_bytes(),newer)

    async def test_idempotent_install_and_owned_uninstall_preserve_later_edits(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=private_directory(root/'home');journal=Journal(private_directory(root/'state'))
            (home/'.claude').mkdir();(home/'.codex').mkdir()
            original={'model':'unchanged','featureDefault':False,'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':'existing-hook'}]}]}}
            settings=home/'.claude/settings.json';settings.write_text(json.dumps(original))
            (home/'.codex/hooks.json').write_text(json.dumps({'hooks':{'SessionStart':[{'hooks':[{'type':'command','command':'existing-codex'}]}]}}))
            (home/'.codex/config.toml').write_text('[features]\ndaemon_auto_start = false\n')
            installer=Installer(journal,home=home,native=False)
            before=installer.plan();self.assertTrue(before['operations'])
            await installer.install();manifest=installer.manifest.read_bytes();settings_bytes=settings.read_bytes()
            again=await installer.install();self.assertFalse(again['changed']);self.assertEqual(manifest,installer.manifest.read_bytes());self.assertEqual(settings_bytes,settings.read_bytes())
            changed=json.loads(settings.read_text());changed['laterUnrelated']={'enabled':False};changed['hooks']['SessionStart'].append({'hooks':[{'type':'command','command':'later-hook'}]});settings.write_text(json.dumps(changed))
            result=await installer.uninstall();self.assertTrue(result['uninstalled'])
            after=json.loads(settings.read_text());self.assertEqual(after['laterUnrelated'],{'enabled':False});self.assertEqual(after['featureDefault'],False)
            self.assertEqual([h['command'] for g in after['hooks']['SessionStart'] for h in g['hooks']],['existing-hook','later-hook'])
            self.assertFalse(installer.command.exists());self.assertTrue(journal.root.exists());journal.close()

    async def test_command_collision_is_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);home=private_directory(root/'home');journal=Journal(private_directory(root/'state'))
            command=home/'.local/bin/npeer';command.parent.mkdir(parents=True);command.write_text('unrelated command')
            installer=Installer(journal,home=home,native=False);self.assertTrue(installer.plan()['command_collision'])
            await installer.install();self.assertEqual(command.read_text(),'unrelated command');await installer.uninstall();self.assertEqual(command.read_text(),'unrelated command');journal.close()
