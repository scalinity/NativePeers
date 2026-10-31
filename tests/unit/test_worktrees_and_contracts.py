import json,tempfile,unittest,subprocess
from pathlib import Path
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.protocol import validate
from nativepeers.worktrees import git,prepare,snapshot

ROOT=Path(__file__).resolve().parents[2]
class WorkspaceTests(unittest.TestCase):
 def test_isolated_dirty_worktree_preserves_original_and_excludes_secrets(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);repo=root/'repo';repo.mkdir();git(repo,'init','-q');(repo/'fixture.txt').write_text('baseline\n');git(repo,'add','fixture.txt');git(repo,'commit','-qm','Create fixture baseline')
   (repo/'fixture.txt').write_text('dirty\n');(repo/'chosen.txt').write_text('selected\n');(repo/'.env').write_text('fixture-secret-not-real\n');before=snapshot(repo);j=Journal(root/'state')
   target,_,baseline=prepare(j,repo,'fixture-worker',{'mode':'isolated-worktree','untracked_allowlist':['chosen.txt']})
   self.assertEqual(snapshot(repo),before);self.assertEqual(Path(target,'fixture.txt').read_text(),'dirty\n');self.assertEqual(Path(target,'chosen.txt').read_text(),'selected\n');self.assertFalse(Path(target,'.env').exists());self.assertEqual({k:v for k,v in baseline.items() if k!='input_tree'},snapshot(repo,untracked_allowlist=['chosen.txt']))
   with self.assertRaises(PeerError):prepare(j,repo,'secret-worker',{'mode':'isolated-worktree','untracked_allowlist':['.env']})
   j.close()
 def test_spawn_examples_and_bad_types(self):
  schema=json.loads((ROOT/'schemas/spawn.v1.json').read_text())
  for p in (ROOT/'examples').glob('*.json'):
   value=json.loads(p.read_text())
   if 'workers' in value:validate(value,schema)
  for value in [{'workers':[]},{'workers':[{'provider':'codex','title':'x','task':'x'}],'limits':{'max_depth':True}},{'workers':[{'provider':'codex','title':'x','task':'x','from':'spoof'}]}]:
   with self.assertRaises(PeerError):validate(value,schema)
 def test_non_git_analysis_needs_no_snapshot_copy(self):
  with tempfile.TemporaryDirectory() as d:
   j=Journal(Path(d)/'state');path,worktree,baseline=prepare(j,d,'read-only',{'mode':'inherit-read-only'});self.assertIsNone(worktree);self.assertFalse(baseline['git']);j.close()
