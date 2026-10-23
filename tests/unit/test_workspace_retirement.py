"""Real disposable Git worktrees; no native models or user workspace removal."""
import asyncio
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import time
import unittest
from unittest.mock import patch

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.files import advisory_lock, atomic_json, canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.worktrees import git, prepare
from nativepeers.workspace_retirement import archive_retire, retirement, retain_processes


class RetirementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve(); self.source = self.root/'source'; self.source.mkdir()
        self.j = Journal(self.root/'state'); self.addCleanup(self.j.close)
        self.enterContext(patch('nativepeers.worktrees.ROOT', self.root/'project'))
        peer = Registry(self.j).register('codex', self.root/'home', new_id(), 'parent', identity(os.getpid()).json(), str(self.source), {}, owner_kind='test')
        self.caller = Caller('peer', peer['handle'], peer['incarnation_id'])
        child = subprocess.Popen(['/bin/sleep', '.05']); self.dead = identity(child.pid).json(); child.wait()
        (self.source/'input').write_text('base\n')
        git(self.source, 'init', '-q'); git(self.source, 'add', 'input'); git(self.source, 'commit', '-qm', 'Create retirement fixture')
        self.worker = new_id(); self.group = new_id(); self.native = new_id()
        directory, _, baseline = prepare(self.j, self.source, self.worker, {'mode': 'isolated-worktree'})
        self.work = Path(directory); self.baseline = baseline
        lease = {'parent_key': self.caller.key, 'baseline': baseline, 'spec': {'cwd': directory}}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (self.group, self.caller.key, '{}', 'COMPLETED', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (self.worker, self.group, None, 'codex', self.native, 'fixture', 'fixture', 'fixture', None, directory, self.worker, 'RELEASED_RESUMABLE', 1, canonical(lease).decode()))
        self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (new_id(), self.worker, None, None, 'COMPLETED', None, None, 1, '{}'))
        self.manifest = self.j.root/'workers'/(self.worker+'.json')
        atomic_json(self.manifest, {'worker_id': self.worker, 'group_id': self.group, 'native_id': self.native, 'generation': 1,
                                  'owner': self.dead, 'native_process': None, 'cleanup': {'state': 'CLOSED', 'output_eof': True}})
        atomic_json(self.j.root/'groups'/self.group/'owner.json', {'owner': self.dead, 'group_id': self.group, 'generation': 1})
        retain_processes(self.j, self.worker, 1, self.native, [self.dead])

    def retire(self): return archive_retire(self.j, self.caller, self.worker)

    def refuse(self, code=None):
        with self.assertRaises(PeerError) as error: self.retire()
        if code: self.assertEqual(error.exception.code, code)
        self.assertTrue(self.work.exists())
        return error.exception

    def test_dirty_ignored_untracked_staged_and_unique_commits_recover_without_histories(self):
        (self.work/'input').write_text('committed unique\n'); git(self.work, 'add', 'input'); git(self.work, 'commit', '-qm', 'Preserve unique work')
        head = git(self.work, 'rev-parse', 'HEAD').stdout.decode().strip()
        (self.work/'input').write_text('staged unique\n'); git(self.work, 'add', 'input'); (self.work/'input').write_text('unstaged unique\n')
        (self.work/'binary').write_bytes(b'\0private untracked')
        (self.work/'.gitignore').write_text('ignored\n'); (self.work/'ignored').write_text('ignored unique')
        (self.work/'empty').mkdir(); os.chmod(self.work/'empty', 0o750)
        original_manifest = self.manifest.read_bytes(); tasks = self.j.all('SELECT * FROM tasks')
        result = self.retire(); self.assertEqual(result['state'], 'retired'); self.assertFalse(self.work.exists())
        self.assertEqual(self.manifest.read_bytes(), original_manifest); self.assertEqual(self.j.all('SELECT * FROM tasks'), tasks)
        self.assertTrue(json.loads(self.manifest.read_bytes())['cleanup']['output_eof'])
        self.assertNotEqual(git(self.source, 'show-ref', '--verify', 'refs/heads/npeer/'+self.worker, check=False).returncode, 0)
        recovered = self.root/'recovered'; recovered.mkdir()
        with tarfile.open(result['archive']) as archive:
            archive.extractall(recovered, filter='data')
        restored = recovered/'workspace'; (restored/'.git').unlink(); git(restored, 'init', '-q')
        git(restored, 'unpack-objects', input=(recovered/'git/objects.pack').read_bytes())
        git(restored, 'update-ref', 'HEAD', head); (restored/'.git/index').write_bytes((recovered/'git/index').read_bytes())
        self.assertEqual((restored/'input').read_text(), 'unstaged unique\n')
        self.assertEqual(git(restored, 'show', ':input').stdout, b'staged unique\n')
        self.assertEqual(git(restored, 'show', 'HEAD:input').stdout, b'committed unique\n')
        self.assertEqual((restored/'ignored').read_text(), 'ignored unique')
        self.assertEqual((restored/'binary').read_bytes(), b'\0private untracked')
        verify = git(restored, 'bundle', 'verify', str(recovered/'git/branch.bundle'), check=False)
        self.assertEqual(verify.returncode, 0)
        receipt = json.loads(Path(result['receipt_path']).read_bytes()); self.assertEqual(receipt['snapshot']['directories']['empty'], 0o750)

    def test_repeat_after_success_verifies_same_backup_and_never_launches(self):
        first = self.retire(); self.assertEqual(self.retire(), first)
        from nativepeers.recovery import assert_released
        with self.assertRaises(PeerError) as error: assert_released(self.j, self.j.one('SELECT * FROM workers'))
        self.assertEqual(error.exception.code, 'WORKSPACE_RETIRED')

    def test_complete_captured_births_do_not_replace_unproved_output_closure(self):
        value = json.loads(self.manifest.read_bytes()); value['cleanup'] = {'state': 'UNCERTAIN', 'output_eof': False}; atomic_json(self.manifest, value)
        with patch('nativepeers.workspace_retirement._previous_boot', return_value=None):
            self.refuse('CLEANUP_UNCERTAIN')
            archived = archive_retire(self.j, self.caller, self.worker, archive_only=True)
        self.assertEqual(archived['state'], 'archived'); self.assertTrue(self.work.exists())
        with patch('nativepeers.workspace_retirement._previous_boot', return_value={'basis': 'previous-boot-ended'}):
            self.assertEqual(self.retire()['state'], 'retired')
        self.assertFalse(json.loads(self.manifest.read_bytes())['cleanup']['output_eof'])

    def test_later_boot_does_not_override_a_known_live_writer(self):
        value = json.loads(self.manifest.read_bytes()); value['owner'] = identity(os.getpid()).json(); atomic_json(self.manifest, value)
        with patch('nativepeers.workspace_retirement._previous_boot', return_value={'basis': 'previous-boot-ended'}):
            self.refuse('OWNERSHIP_CONFLICT')

    def test_conflict_index_blobs_and_uncommitted_frozen_input_tree_are_recoverable(self):
        blobs = [git(self.work, 'hash-object', '-w', '--stdin', input=value).stdout.decode().strip()
                 for value in (b'conflict base', b'conflict ours', b'conflict theirs', b'frozen dirty input')]
        tree = git(self.work, 'mktree', input=('100644 blob '+blobs[3]+'\tinput\n').encode()).stdout.decode().strip()
        row = self.j.one('SELECT * FROM workers'); lease = json.loads(row['lease_json']); lease['baseline']['input_tree'] = tree
        self.j.db.execute('UPDATE workers SET lease_json=?', (canonical(lease).decode(),))
        git(self.work, 'update-index', '--force-remove', 'input')
        git(self.work, 'update-index', '--index-info', input=''.join('100644 '+blob+' '+str(n)+'\tinput\n' for n, blob in enumerate(blobs[:3], 1)).encode())
        result = self.retire(); recovery = self.root/'conflict-recovery'; recovery.mkdir()
        with tarfile.open(result['archive']) as archive: archive.extractall(recovery, filter='data')
        restored = recovery/'workspace'; (restored/'.git').unlink(); git(restored, 'init', '-q')
        git(restored, 'unpack-objects', input=(recovery/'git/objects.pack').read_bytes())
        (restored/'.git/index').write_bytes((recovery/'git/index').read_bytes())
        for n, expected in enumerate((b'conflict base', b'conflict ours', b'conflict theirs'), 1):
            self.assertEqual(git(restored, 'show', ':'+str(n)+':input').stdout, expected)
        self.assertEqual(git(restored, 'show', tree+':input').stdout, b'frozen dirty input')

    def test_owned_branch_reflog_and_private_worktree_ref_roots_survive_retirement(self):
        base = git(self.work, 'rev-parse', 'HEAD').stdout.decode().strip()
        tree = git(self.work, 'rev-parse', 'HEAD^{tree}').stdout.decode().strip()
        def commit(message):
            return git(self.work, 'commit-tree', tree, '-p', base, input=message.encode()).stdout.decode().strip()
        branch_only = commit('branch reflog only')
        private_old = commit('private reflog old')
        private_current = commit('private ref current')
        branch = 'refs/heads/npeer/'+self.worker
        # Change the branch directly; this intentionally does not touch HEAD's reflog.
        git(self.source, 'update-ref', '-m', 'retain branch-only object', branch, branch_only)
        git(self.source, 'update-ref', '-m', 'restore branch', branch, base)
        private_ref = 'refs/worktree/recovery-fixture'
        git(self.work, 'update-ref', '--create-reflog', private_ref, private_old)
        git(self.work, 'update-ref', '-m', 'retain old private object', private_ref, private_current)
        git(self.work, 'symbolic-ref', 'refs/rewritten/recovery-symbolic', private_ref)
        head_log = git(self.work, 'reflog', 'show', '--format=%H', 'HEAD').stdout.decode()
        self.assertNotIn(branch_only, head_log)
        result = self.retire(); recovery = self.root/'refs-recovery'; recovery.mkdir()
        with tarfile.open(result['archive']) as archive: archive.extractall(recovery, filter='data')
        restored = recovery/'workspace'; (restored/'.git').unlink(); git(restored, 'init', '-q')
        git(restored, 'unpack-objects', input=(recovery/'git/objects.pack').read_bytes())
        for oid in (branch_only, private_old, private_current):
            self.assertEqual(git(restored, 'cat-file', '-t', oid).stdout, b'commit\n')
        refs = json.loads((recovery/'git/worktree-refs.json').read_bytes())
        for name, value in refs.items():
            if value['symbolic_target']: git(restored, 'symbolic-ref', name, value['symbolic_target'])
            else: git(restored, 'update-ref', name, value['oid'])
        self.assertEqual(git(restored, 'rev-parse', private_ref).stdout.decode().strip(), private_current)
        self.assertEqual(git(restored, 'symbolic-ref', 'refs/rewritten/recovery-symbolic').stdout.decode().strip(), private_ref)
        self.assertIn(branch_only, (recovery/'git/metadata/logs'/branch).read_text())

    def test_live_and_unknown_captured_births_refuse(self):
        value = json.loads(self.manifest.read_bytes()); value['owner'] = identity(os.getpid()).json(); atomic_json(self.manifest, value)
        self.refuse('OWNERSHIP_CONFLICT')
        value['owner'] = self.dead; atomic_json(self.manifest, value)
        with patch('nativepeers.workspace_retirement.owned_status', return_value='UNKNOWN'): self.refuse('OWNERSHIP_UNKNOWN')

    def test_live_registered_history_and_old_group_birth_refuse_even_offline_label(self):
        owner = identity(os.getpid()).json()
        descriptor = {'owner': owner, 'home': str(self.root/'home'), 'transport': {}}
        self.j.db.execute('INSERT INTO peer_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('old', new_id(), self.native, 'codex', 'home', owner['pid'], owner['start'], 'fixture', 'owned-worker-controller', canonical(descriptor).decode(), 'offline', 0))
        self.refuse('OWNERSHIP_CONFLICT')

    def test_missing_or_malformed_birth_refuses(self):
        value = json.loads(self.manifest.read_bytes()); value['owner'].pop('start'); atomic_json(self.manifest, value)
        self.refuse('OWNERSHIP_UNKNOWN')

    def test_active_tasks_and_generation_races_refuse_without_deletion(self):
        self.j.db.execute("UPDATE tasks SET state='RUNNING'"); self.refuse('OWNERSHIP_CONFLICT')
        self.j.db.execute("UPDATE tasks SET state='COMPLETED'")
        from nativepeers import workspace_retirement as module
        original = module._archive
        def race(*args):
            value = original(*args); self.j.db.execute('UPDATE workers SET generation=2'); return value
        with patch.object(module, '_archive', side_effect=race): self.refuse('OWNERSHIP_CONFLICT')
        self.assertIsNone(retirement(self.j, self.worker))

    def test_resume_lock_is_shared_and_real_resume_cannot_enter_during_archive(self):
        from nativepeers import workspace_retirement as module
        from nativepeers.recovery import resume_worker
        original = module._archive; checked = []; futures = []
        def attempt():
            j = Journal.existing(self.j.root, timeout=5)
            try:
                with self.assertRaises(PeerError) as error:
                    asyncio.run(resume_worker(j, self.caller, self.worker, 'must not run'))
                return error.exception.code
            finally: j.close()
        pool = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        self.addCleanup(pool.shutdown)
        def under_lock(*args):
            # An exact real resume waits for this lock before any native admission.
            future = pool.submit(attempt); futures.append(future); time.sleep(.05)
            self.assertFalse(future.done())
            checked.append(True); return original(*args)
        with patch.object(module, '_archive', side_effect=under_lock): self.retire()
        self.assertEqual(futures[0].result(timeout=5), 'WORKSPACE_RETIRED')
        self.assertEqual(checked, [True]); self.assertEqual(len(self.j.all('SELECT * FROM tasks')), 1)

    def test_resumed_generations_require_all_registered_controller_births(self):
        self.j.db.execute('UPDATE workers SET generation=2')
        value = json.loads(self.manifest.read_bytes()); value['generation'] = 2; atomic_json(self.manifest, value)
        self.refuse('OWNERSHIP_UNKNOWN')
        retain_processes(self.j, self.worker, 2, self.native, [self.dead])
        for n in range(2):
            descriptor = {'owner': dict(self.dead, start=self.dead['start']+'-'+str(n)), 'home': str(self.root/'home'), 'transport': {'kind': 'owned-worker', 'worker_id': self.worker}}
            self.j.db.execute('INSERT INTO peer_instances VALUES(?,?,?,?,?,?,?,?,?,?,?,?)', ('old-'+str(n), new_id(), self.native, 'codex', 'home', self.dead['pid'], descriptor['owner']['start'], 'fixture', 'owned-worker-controller', canonical(descriptor).decode(), 'offline', 0))
        self.assertEqual(self.retire()['state'], 'retired')

    def test_missing_old_descendant_snapshot_cannot_be_reconstructed_from_dead_leaders(self):
        self.j.db.execute("DELETE FROM events WHERE kind='workspace-process-snapshot'")
        with patch('nativepeers.workspace_retirement._previous_boot', return_value=None): error = self.refuse('OWNERSHIP_UNKNOWN')
        self.assertIn('not retained', error.message)

    def test_archive_only_keeps_incomplete_old_history_and_workspace_unfenced(self):
        self.j.db.execute("DELETE FROM events WHERE kind='workspace-process-snapshot'")
        with patch('nativepeers.workspace_retirement._previous_boot', return_value=None):
            result = archive_retire(self.j, self.caller, self.worker, archive_only=True)
            self.assertEqual(result['state'], 'archived'); self.assertFalse(result['removed'])
            self.assertTrue(self.work.exists()); self.assertIsNone(retirement(self.j, self.worker))
            self.refuse('OWNERSHIP_UNKNOWN')
        with patch('nativepeers.workspace_retirement._previous_boot', return_value={'basis': 'previous-boot-ended', 'boot_started_at': 9999999999}):
            retired = self.retire()
        self.assertEqual(retired['archive_sha256'], result['archive_sha256']); self.assertEqual(retired['state'], 'retired')

    def test_boot_proof_uses_real_captured_birth_times_not_dead_leader_assumptions(self):
        from nativepeers.workspace_retirement import _previous_boot
        import sys
        if sys.platform != 'darwin': self.skipTest('Darwin boot timestamp parser')
        result = subprocess.CompletedProcess([], 0, b'{ sec = 1000, usec = 42 } fixture\n')
        with patch('nativepeers.workspace_retirement.subprocess.run', return_value=result):
            self.assertIsNotNone(_previous_boot([dict(self.dead, start='999:1')]))
            self.assertIsNone(_previous_boot([dict(self.dead, start='1001:1')]))
            self.assertIsNone(_previous_boot([dict(self.dead, start='missing')]))

    def test_snapshot_retains_old_provider_descendants_after_manifest_reset(self):
        live = identity(os.getpid()).json()
        retain_processes(self.j, self.worker, 1, self.native, [self.dead, live])
        retain_processes(self.j, self.worker, 1, self.native, [self.dead])
        self.refuse('OWNERSHIP_CONFLICT')

    def test_symlinks_special_files_and_redirected_git_pointer_refuse(self):
        (self.work/'link').symlink_to(self.source/'input'); self.refuse(); (self.work/'link').unlink()
        (self.work/'.git').unlink(); (self.work/'.git').symlink_to(self.source/'.git/config'); self.refuse('OWNERSHIP_UNKNOWN')

    def test_internal_symlink_is_saved_as_inert_data_without_following(self):
        (self.work/'AGENTS.md').symlink_to('input')
        result = self.retire(); receipt = json.loads(Path(result['receipt_path']).read_bytes())
        self.assertEqual(receipt['snapshot']['files']['AGENTS.md']['link_target'], 'input')
        with tarfile.open(result['archive']) as archive:
            entry = archive.getmember('workspace/AGENTS.md')
            self.assertTrue(entry.isfile()); self.assertEqual(archive.extractfile(entry).read(), b'input')

    def test_archive_failure_does_not_fence_or_delete_workspace(self):
        with patch('nativepeers.workspace_retirement._verify', side_effect=RuntimeError('backup verification failed')):
            with self.assertRaises(RuntimeError): self.retire()
        self.assertTrue(self.work.exists()); self.assertIsNone(retirement(self.j, self.worker))
        self.assertEqual(self.retire()['state'], 'retired')

    def test_archive_tamper_blocks_retry_after_interrupted_remove(self):
        from nativepeers import workspace_retirement as module
        original = module._git
        def fail_remove(cwd, args, *other, **kwargs):
            if args[:2] == ['worktree', 'remove']: raise RuntimeError('simulated interruption')
            return original(cwd, args, *other, **kwargs)
        with patch.object(module, '_git', side_effect=fail_remove), self.assertRaises(RuntimeError): self.retire()
        prepared = retirement(self.j, self.worker); self.assertEqual(prepared['state'], 'prepared')
        archive = Path(prepared['receipt']['archive']); archive.write_bytes(archive.read_bytes()+b'tamper')
        self.refuse('BACKUP_CHANGED')

    def test_interrupted_remove_retry_completes_same_archive(self):
        from nativepeers import workspace_retirement as module
        original = module._git
        def fail_remove(cwd, args, *other, **kwargs):
            if args[:2] == ['worktree', 'remove']: raise RuntimeError('simulated interruption')
            return original(cwd, args, *other, **kwargs)
        with patch.object(module, '_git', side_effect=fail_remove), self.assertRaises(RuntimeError): self.retire()
        before = retirement(self.j, self.worker)['receipt']; result = self.retire()
        self.assertEqual(result['archive_sha256'], before['archive_sha256']); self.assertFalse(self.work.exists())

    def test_interruption_after_git_remove_finishes_branch_and_receipt_on_retry(self):
        from nativepeers import workspace_retirement as module
        original = module._git
        def fail_ref(cwd, args, *other, **kwargs):
            if args[:2] == ['update-ref', '-d']: raise RuntimeError('interrupted after worktree removal')
            return original(cwd, args, *other, **kwargs)
        with patch.object(module, '_git', side_effect=fail_ref), self.assertRaises(RuntimeError): self.retire()
        self.assertFalse(self.work.exists()); self.assertEqual(retirement(self.j, self.worker)['state'], 'prepared')
        self.assertEqual(self.retire()['state'], 'retired')

    def test_staged_only_changes_after_prepared_are_not_discarded(self):
        from nativepeers import workspace_retirement as module
        original = module._git
        def fail_remove(cwd, args, *other, **kwargs):
            if args[:2] == ['worktree', 'remove']: raise RuntimeError('simulated interruption')
            return original(cwd, args, *other, **kwargs)
        with patch.object(module, '_git', side_effect=fail_remove), self.assertRaises(RuntimeError): self.retire()
        (self.work/'input').write_text('late stage'); git(self.work, 'add', 'input'); (self.work/'input').write_text('base\n')
        self.refuse('WORKSPACE_CHANGED')

    def test_branch_reflog_change_blocks_reuse_even_when_branch_head_is_restored(self):
        archive_retire(self.j, self.caller, self.worker, archive_only=True)
        base = git(self.work, 'rev-parse', 'HEAD').stdout.decode().strip()
        tree = git(self.work, 'rev-parse', 'HEAD^{tree}').stdout.decode().strip()
        extra = git(self.work, 'commit-tree', tree, '-p', base, input=b'late branch-only object').stdout.decode().strip()
        branch = 'refs/heads/npeer/'+self.worker
        git(self.source, 'update-ref', branch, extra); git(self.source, 'update-ref', branch, base)
        self.refuse('BACKUP_CHANGED')

    def test_private_ref_change_blocks_reuse(self):
        archive_retire(self.j, self.caller, self.worker, archive_only=True)
        base = git(self.work, 'rev-parse', 'HEAD').stdout.decode().strip()
        git(self.work, 'update-ref', 'refs/bisect/recovery-fixture', base)
        self.refuse('BACKUP_CHANGED')

    def test_receipt_tamper_and_other_parent_are_refused(self):
        result = self.retire(); receipt = Path(result['receipt_path']); receipt.write_text('{}')
        with self.assertRaises(PeerError) as error: self.retire()
        self.assertEqual(error.exception.code, 'BACKUP_CHANGED')
        with self.assertRaises(PeerError) as error: archive_retire(self.j, Caller('external-local', 'external'), self.worker)
        self.assertEqual(error.exception.code, 'CALLER_UNBOUND')

    def test_source_change_after_prepared_is_preserved_and_resume_stays_fenced(self):
        from nativepeers import workspace_retirement as module
        original = module._git
        def fail_remove(cwd, args, *other, **kwargs):
            if args[:2] == ['worktree', 'remove']: raise RuntimeError('simulated interruption')
            return original(cwd, args, *other, **kwargs)
        with patch.object(module, '_git', side_effect=fail_remove), self.assertRaises(RuntimeError): self.retire()
        (self.work/'input').write_text('late data'); self.refuse('WORKSPACE_CHANGED')
        self.assertEqual((self.work/'input').read_text(), 'late data')

    def test_cli_requires_exact_single_selection_and_unchanged_plan_is_read_only(self):
        from nativepeers.cli import parser, dispatch
        with patch('nativepeers.cli.bind_caller', return_value=self.caller):
            with self.assertRaises(PeerError): asyncio.run(dispatch(parser().parse_args(['cleanup', '--archive-retire']), self.j))
            before = self.j.db.total_changes
            value = asyncio.run(dispatch(parser().parse_args(['cleanup', '--plan', '--worker-id', self.worker]), self.j))
            self.assertTrue(value['planner_only']); self.assertEqual(before, self.j.db.total_changes)
            result = asyncio.run(dispatch(parser().parse_args(['cleanup', '--archive-retire', '--worker-id', self.worker]), self.j))
        self.assertEqual(result['state'], 'retired')
