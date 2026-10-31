"""Installed entrypoints pin imports without retargeting the caller's workspace."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
INSTALLED = '/Users/danny/Documents/Tools/NativePeers'


class LauncherTests(unittest.TestCase):
    def test_owned_antigravity_resume_and_mcp_routes_also_exclude_cwd(self):
        from nativepeers.antigravity_install import desired
        home=Path('/fixture-home')
        config=desired(home)[home/'.gemini/config/mcp_config.json'][2]
        self.assertEqual(config['args'][:3],['-P','-m','nativepeers.mcp_server'])
        self.assertEqual(config['env']['PYTHONPATH'],str(ROOT))
        self.assertIn('/.venv/bin/python -P -m nativepeers.antigravity resolve-name',
                      (ROOT/'integrations/zsh/gemini.zsh').read_text())

    def test_all_owned_shell_entrypoints_ignore_cwd_packages_and_keep_selection(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            install = root/'installed';(install/'bin').mkdir(parents=True)
            (install/'.venv/bin').mkdir(parents=True)
            (install/'.venv/bin/python').symlink_to(sys.executable)
            package = install/'nativepeers';package.mkdir()
            (package/'__init__.py').write_text('')
            probe = "import json,os,sys,nativepeers; print(json.dumps(dict(module=nativepeers.__file__,root=os.path.dirname(os.path.dirname(nativepeers.__file__)),python=sys.executable,cwd=os.getcwd(),argv=sys.argv[1:])))\n"
            for name in ['__main__.py','hooks.py','antigravity.py','grok_hooks.py']:
                (package/name).write_text(probe)
            (package/'usage').mkdir();(package/'usage/__init__.py').write_text('')
            (package/'usage/statusline.py').write_text(probe)
            for launcher in (ROOT/'bin').iterdir():
                text = launcher.read_text().replace(INSTALLED, str(install))
                target = install/'bin'/launcher.name;target.write_text(text);target.chmod(0o755)
                for kind in ['unrelated-repo','unprovisioned-worktree']:
                    cwd=root/kind;shadow=cwd/'nativepeers';shadow.mkdir(parents=True,exist_ok=True)
                    sentinel=cwd/'sentinel'
                    (shadow/'__init__.py').write_text(f"from pathlib import Path; Path({str(sentinel)!r}).write_text('shadow executed'); raise RuntimeError('CWD package imported')")
                    with self.subTest(launcher=launcher.name,cwd=kind):
                        result=subprocess.run([str(target),'--project',str(cwd),'--json'],cwd=cwd,env=dict(os.environ,PYTHONPATH=str(cwd)),capture_output=True,text=True)
                        self.assertEqual(result.returncode,0,result.stderr)
                        data=json.loads(result.stdout)
                        self.assertEqual(data['module'],str(package/'__init__.py'))
                        self.assertEqual(data['root'],str(install))
                        self.assertEqual(Path(data['python']).resolve(),Path(sys.executable).resolve())
                        self.assertEqual(data['cwd'],str(cwd))
                        self.assertEqual(data['argv'][-3:],['--project',str(cwd),'--json'])
                        self.assertFalse(sentinel.exists())

    def test_explicit_candidate_module_execution_remains_candidate_bound(self):
        env=dict(os.environ);env.pop('PYTHONPATH',None)
        result=subprocess.run([sys.executable,'-c','import nativepeers; print(nativepeers.__file__)'],cwd=ROOT,env=env,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        self.assertEqual(Path(result.stdout.strip()),ROOT/'nativepeers/__init__.py')
