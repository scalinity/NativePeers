import asyncio
import gzip
import json
import os
from pathlib import Path
import stat
import tempfile
import time
import unittest
from unittest.mock import patch

import jsonschema

from nativepeers.activity import Reader, Recorder, store
from nativepeers.journal import Journal
from nativepeers.models import new_id

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'schemas/activity.v1.json').read_text())
SHA = 'ab' * 32


def lines(journal, worker):
    path = store.active_path(journal.root, worker)
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


def kinds(events):
    return [e['kind'] for e in events]


class RecorderCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.journal = Journal(Path(self.directory.name))
        self.addCleanup(self.journal.close)
        self.worker = new_id()

    def recorder(self, provider='codex', **kwargs):
        return Recorder(self.journal, self.worker, 'group-1', provider, lease={'verified_runtime': {'sha256': SHA}}, cwd='/work/repo', **kwargs)

    async def finish(self, recorder, status='completed', text='done'):
        recorder.terminal(status, text)
        await recorder.flush()


class RecorderTests(RecorderCase):
    async def test_envelope_and_lifecycle(self):
        r = self.recorder(turn=lambda: 'turn_9')
        r.begin('task-1', 1, origin='parent-model', client_request_id='req-1', input_preview='fix the bug')
        r.emit('message.completed', dict(text='hello', is_final_candidate=True), item='m1', event='item/completed')
        await self.finish(r)
        events = lines(self.journal, self.worker)
        self.assertEqual(kinds(events), ['task.started', 'task.state', 'message.completed', 'task.terminal'])
        self.assertEqual([e['seq'] for e in events], [1, 2, 3, 4])
        started, _, message, terminal = events
        self.assertEqual(started['data'], dict(origin='parent-model', client_request_id='req-1', input_preview='fix the bug'))
        self.assertEqual(message['item'], 'm1')
        self.assertEqual(message['source'], dict(provider='codex', runtime_sha256=SHA, event='item/completed'))
        self.assertEqual((message['worker_id'], message['group_id'], message['task_id'], message['generation'], message['native_turn']),
                         (self.worker, 'group-1', 'task-1', 1, 'turn_9'))
        self.assertEqual(terminal['data'], dict(status='completed', final_text='done'))
        self.assertRegex(message['at'], r'^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$')

    async def test_storage_is_private(self):
        r = self.recorder()
        r.begin('task-1', 1)
        await self.finish(r)
        directory, path = store.directory(self.journal.root), store.active_path(self.journal.root, self.worker)
        self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    async def test_seq_and_index_continue_across_a_resumed_controller(self):
        first = self.recorder()
        first.begin('task-1', 1)
        first.emit('message.completed', dict(text='a', is_final_candidate=True))
        await self.finish(first)
        await first.close()
        second = self.recorder()
        second.begin('task-2', 2)
        await self.finish(second, 'failed', 'x')
        await second.close()
        events = lines(self.journal, self.worker)
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        self.assertEqual([(e['generation'], e['task_id']) for e in events if e['kind'] == 'task.started'], [(1, 'task-1'), (2, 'task-2')])
        rows = self.journal.all('SELECT generation,task_id,first_seq,last_seq,terminal_seq FROM activity_index WHERE worker_id=? ORDER BY first_seq', (self.worker,))
        self.assertEqual(rows, [dict(generation=1, task_id='task-1', first_seq=1, last_seq=4, terminal_seq=4),
                                dict(generation=2, task_id='task-2', first_seq=5, last_seq=7, terminal_seq=7)])

    async def test_a_seq_is_never_reused_even_when_the_log_is_gone(self):
        first = self.recorder()
        first.begin('task-1', 1)
        await self.finish(first)
        await first.close()
        for path in store.logs(self.journal.root, self.worker):
            path.unlink()
        second = self.recorder()
        second.begin('task-2', 2)
        await self.finish(second)
        self.assertEqual([e['seq'] for e in lines(self.journal, self.worker)], [4, 5, 6])

    async def test_a_crashed_generation_is_closed_before_the_next_one_starts(self):
        crashed = self.recorder()
        crashed.begin('task-1', 1)
        crashed.emit('tool.started', dict(kind='exec', title='sleep', input={}), item='cmd')
        await crashed.flush()
        successor = self.recorder()
        successor.begin('task-2', 2)
        await self.finish(successor)
        events = lines(self.journal, self.worker)
        self.assertEqual(kinds(events), ['task.started', 'task.state', 'tool.started', 'tool.completed', 'task.terminal', 'task.started', 'task.state', 'task.terminal'])
        closing = events[3:5]
        self.assertEqual([(e['generation'], e['task_id']) for e in closing], [(1, 'task-1')] * 2)
        self.assertEqual(closing[0]['data']['status'], 'interrupted')
        self.assertEqual(closing[1]['data']['status'], 'interrupted')
        self.assertEqual(closing[1]['data']['error']['code'], 'DELIVERY_UNKNOWN_AFTER_OWNER_LOSS')
        self.assertEqual([e['seq'] for e in events], list(range(1, 9)))
        self.assertTrue(all(e['generation'] == 2 for e in events[5:]))

    async def test_events_of_an_older_generation_never_follow_a_newer_start(self):
        r = self.recorder()
        r.begin('task-2', 2)
        await self.finish(r)
        r.begin('task-1', 1)
        r.emit('message.completed', dict(text='late', is_final_candidate=False))
        await r.flush()
        events = lines(self.journal, self.worker)
        self.assertEqual(kinds(events), ['task.started', 'task.state', 'task.terminal', 'gap'])
        self.assertEqual(events[-1]['data'], dict(reason='stale-generation', generation=1))

    async def test_a_newer_start_supersedes_a_task_that_never_ended(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.begin('task-2', 1)
        await self.finish(r)
        events = lines(self.journal, self.worker)
        self.assertEqual(kinds(events), ['task.started', 'task.state', 'task.terminal', 'task.started', 'task.state', 'task.terminal'])
        self.assertEqual(events[2]['data']['error']['code'], 'SUPERSEDED')
        self.assertEqual(events[2]['task_id'], 'task-1')

    async def test_terminal_is_once_and_closes_unpaired_tools(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.emit('tool.started', dict(kind='exec', title='a', input={}), item='one')
        r.emit('tool.started', dict(kind='read', title='b', input={}), item='two')
        r.emit('tool.completed', dict(status='ok', output='fine'), item='two')
        self.assertTrue(r.terminal('failed', '', error=dict(code='WORKER_CRASHED', message='boom')))
        self.assertFalse(r.terminal('completed', 'again'))
        r.emit('message.completed', dict(text='after', is_final_candidate=True))
        await r.flush()
        events = lines(self.journal, self.worker)
        self.assertEqual(kinds(events).count('task.terminal'), 1)
        self.assertEqual(kinds(events)[-2:], ['tool.completed', 'task.terminal'])
        self.assertEqual((events[-2]['item'], events[-2]['data']['status']), ('one', 'interrupted'))
        self.assertEqual(events[-1]['data'], dict(status='failed', final_text='', error=dict(code='WORKER_CRASHED', message='boom')))
        self.assertNotIn('message.completed', kinds(events))

    async def test_nothing_is_recorded_outside_a_task(self):
        r = self.recorder()
        r.emit('message.completed', dict(text='orphan', is_final_candidate=True))
        self.assertFalse(r.terminal('completed'))
        await r.flush()
        self.assertEqual(lines(self.journal, self.worker), [])

    async def test_terminal_status_is_recorded_as_the_controller_decided(self):
        for status in ('completed', 'interrupted', 'failed', 'canceled', 'timed_out', 'nonsense'):
            with self.subTest(status=status):
                r = Recorder(self.journal, new_id(), 'g', 'claude')
                r.begin('t', 1)
                r.terminal(status)
                await r.flush()
                self.assertEqual(lines(self.journal, r.worker_id)[-1]['data']['status'], status if status != 'nonsense' else 'failed')

    async def test_native_status_is_kept_beside_the_controllers_status(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.task.normalizer.turn = None
        r.feed(dict(method='turn/completed', params=dict(threadId='t', turn=dict(id='u', items=[], status='interrupted'))))
        r.terminal('canceled')
        await r.flush()
        terminal = lines(self.journal, self.worker)[-1]
        self.assertEqual((terminal['data']['status'], terminal['data']['native_status']), ('canceled', 'interrupted'))
        self.assertEqual(terminal['source']['event'], 'turn/completed')

    async def test_a_torn_final_line_is_skipped_by_readers_then_removed_by_the_next_writer(self):
        r = self.recorder()
        r.begin('task-1', 1)
        await r.flush()
        path = store.active_path(self.journal.root, self.worker)
        with open(path, 'ab') as stream:
            stream.write(b'{"schema":"nativepeers.activity.v1","seq":3,"kind":"mess')
        self.assertEqual([e['seq'] for e in Reader(self.journal.root, self.worker).read(0)['events']], [1, 2])
        await r.close()
        successor = self.recorder()
        successor.begin('task-2', 2)
        await self.finish(successor)
        events = lines(self.journal, self.worker)
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        gap = next(e for e in events if e['kind'] == 'gap')
        self.assertEqual(gap['data']['reason'], 'torn-tail')

    async def test_fsync_happens_at_the_terminal_and_at_least_every_interval_while_events_flow(self):
        calls = []
        real = os.fsync
        with patch('os.fsync', side_effect=lambda fd: (calls.append(time.monotonic()), real(fd))[1]):
            r = self.recorder(fsync_interval=0.05)
            r.begin('task-1', 1)
            await r.flush()
            calls.clear()
            start = time.monotonic()
            for _ in range(20):
                r.emit('message.delta', dict(text='x'))
                await asyncio.sleep(0.02)
            flowing = len(calls)
            r.terminal('completed')
            await asyncio.sleep(0.05)
            await r.flush()
            self.assertGreaterEqual(flowing, 3)
            self.assertLessEqual(flowing, 12)
            self.assertGreater(len(calls), flowing - 1)
            self.assertTrue(r.fd is not None and not r.dirty)
            terminal_synced = [c for c in calls if c >= start]
            self.assertTrue(terminal_synced)
            self.assertEqual(lines(self.journal, self.worker)[-1]['kind'], 'task.terminal')

    async def test_the_terminal_is_fsynced_without_waiting_for_the_interval_or_a_flush(self):
        r = self.recorder(fsync_interval=60)
        r.begin('task-1', 1)
        await r.flush()
        with patch('os.fsync', wraps=os.fsync) as synced:
            r.emit('message.delta', dict(text='x'))
            await asyncio.sleep(0.1)
            self.assertEqual(synced.call_count, 0)
            r.terminal('completed')
            await asyncio.sleep(0.2)
            self.assertGreaterEqual(synced.call_count, 1)
        self.assertFalse(r.dirty)

    async def test_rotation_keeps_every_event_readable(self):
        r = self.recorder()
        r.limits['log_bytes'] = 4000
        r.begin('task-1', 1)
        for number in range(60):
            r.emit('message.completed', dict(text='event %d ' % number + 'x' * 120, is_final_candidate=False))
            if number % 10 == 9:
                await r.flush()
        await self.finish(r)
        parts = store.segments(self.journal.root, self.worker)
        self.assertGreaterEqual(len(parts), 2)
        self.assertEqual([n for n, _ in parts], list(range(1, len(parts) + 1)))
        self.assertTrue(all(stat.S_IMODE(path.stat().st_mode) == 0o600 for _, path in parts))
        self.assertLess(store.active_path(self.journal.root, self.worker).stat().st_size, 4000 + 1000)
        with gzip.open(parts[0][1], 'rb') as stream:
            self.assertTrue(all(json.loads(line)['seq'] for line in stream))
        read = Reader(self.journal.root, self.worker).read(0, limit=512)
        self.assertEqual([e['seq'] for e in read['events']], list(range(1, 64)))
        self.assertEqual(Reader(self.journal.root, self.worker).read(37)['events'][0]['seq'], 38)

    async def test_secrets_are_redacted_before_they_reach_disk(self):
        secrets = ['sk-ant-api03-AbCdEfGhIjKlMnOpQrStUv', 'ghp_' + 'a1B2c3D4e5' * 4, 'hunter2hunter2', 'AKIAABCDEFGHIJKLMNOP', 'eyJhbGciOiJIUzI1.eyJzdWIiOiIxMjM0.SflKxwRJSMeKKF2QT4']
        r = self.recorder()
        r.begin('task-1', 1, input_preview='use token ' + secrets[0])
        r.emit('tool.started', dict(kind='exec', title='env', input=dict(command='export ANTHROPIC_API_KEY=%s; curl -H "Authorization: Bearer %s" https://u:%s@host' % (secrets[0], secrets[1], secrets[2]))), item='c')
        r.emit('tool.completed', dict(status='ok', output='aws=%s\njwt=%s\npassword: %s\n' % (secrets[3], secrets[4], secrets[2])), item='c')
        r.emit('message.completed', dict(text='-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----', is_final_candidate=True))
        await self.finish(r, text='the key was ' + secrets[0])
        raw = store.active_path(self.journal.root, self.worker).read_text()
        for secret in secrets + ['MIIEow']:
            self.assertNotIn(secret, raw)
        self.assertIn('[REDACTED]', raw)

    async def test_bounds_follow_the_defaults_and_config(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.emit('message.completed', dict(text='y' * 200000, is_final_candidate=True))
        r.emit('tool.started', dict(kind='exec', title='cat', input=dict(command='cat big')), item='c')
        r.emit('tool.completed', dict(status='ok', output='HEAD' + 'z' * 40000 + 'TAIL'), item='c')
        r.emit('files.changed', dict(paths=['a'], stats=dict(added=1, removed=0), diff=dict(text='+' + 'd' * 50000)), item='c')
        await self.finish(r, text='f' * 100000)
        events = {e['kind']: e for e in lines(self.journal, self.worker)}
        for event in events.values():
            self.assertLessEqual(len(json.dumps(event['data']).encode()), 65536)
        output = events['tool.completed']['data']['output']
        self.assertLessEqual(len(output['text'].encode()), 16384)
        self.assertEqual((output['truncated'], output['bytes'], output['artifact_id']), (True, 40008, None))
        self.assertTrue(output['text'].startswith('HEAD') and output['text'].endswith('TAIL'))
        self.assertIn('reason', output)
        self.assertTrue(events['files.changed']['data']['diff']['truncated'])
        self.assertTrue(events['message.completed']['data']['text'].startswith('y'))

    async def test_configured_bounds_apply(self):
        (self.journal.root / 'config.toml').write_text('[activity]\nevent_data_bytes = 8192\ntool_output_bytes = 1024\nlog_bytes = 5000000\n')
        r = self.recorder()
        self.assertEqual(r.limits, dict(event_data_bytes=8192, tool_output_bytes=1024, log_bytes=5000000))
        r.begin('task-1', 1)
        r.emit('message.completed', dict(text='y' * 50000, is_final_candidate=True))
        r.emit('tool.started', dict(kind='exec', title='cat', input={}), item='c')
        r.emit('tool.completed', dict(status='ok', output='z' * 5000), item='c')
        await self.finish(r)
        events = {e['kind']: e for e in lines(self.journal, self.worker)}
        self.assertLessEqual(len(json.dumps(events['message.completed']['data']).encode()), 8192)
        self.assertLessEqual(len(events['tool.completed']['data']['output']['text'].encode()), 1024)

    async def test_invalid_config_falls_back_to_defaults(self):
        (self.journal.root / 'config.toml').write_text('[activity]\nevent_data_bytes = 12\nlog_bytes = "big"\ntool_output_bytes = 2048\n')
        self.assertEqual(self.recorder().limits, dict(event_data_bytes=65536, tool_output_bytes=2048, log_bytes=67108864))
        (self.journal.root / 'config.toml').write_text('this is not toml [')
        self.assertEqual(self.recorder().limits['event_data_bytes'], 65536)

    async def test_overflow_drops_into_one_gap_without_blocking_and_keeps_the_terminal(self):
        r = self.recorder(queue_events=8)
        r.begin('task-1', 1)
        start = time.monotonic()
        for number in range(1000):
            r.emit('message.delta', dict(text='chunk %d' % number))
        elapsed = time.monotonic() - start
        self.assertLess(elapsed, 0.5)
        r.terminal('completed', 'ok')
        await r.flush()
        events = lines(self.journal, self.worker)
        gaps = [e for e in events if e['kind'] == 'gap']
        deltas = [e for e in events if e['kind'] == 'message.delta']
        self.assertEqual(len(gaps), 1)
        self.assertEqual(gaps[0]['data']['reason'], 'overflow')
        self.assertEqual(len(deltas) + gaps[0]['data']['dropped'], 1000)
        self.assertLessEqual(len(deltas), 8)
        self.assertEqual(events[-1]['kind'], 'task.terminal')
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events) + 1)))
        self.assertLess(events.index(gaps[0]), events.index(events[-1]))

    async def test_a_gap_sits_where_events_were_lost(self):
        r = self.recorder(queue_events=4)
        r.begin('task-1', 1)
        for number in range(10):
            r.emit('message.delta', dict(text=str(number)))
        await r.flush()
        r.emit('message.delta', dict(text='later'))
        await self.finish(r)
        events = lines(self.journal, self.worker)
        self.assertEqual([(e['kind'], e['data'].get('text')) for e in events],
                         [('task.started', None), ('task.state', None), ('message.delta', '0'), ('message.delta', '1'), ('gap', None),
                          ('message.delta', 'later'), ('task.terminal', None)])
        self.assertEqual(events[4]['data'], dict(reason='overflow', dropped=8))

    async def test_a_normalizer_failure_becomes_one_gap_and_the_task_survives(self):
        r = self.recorder()
        r.begin('task-1', 1)
        broken = dict(method='item/started', params=dict(item=dict(type='commandExecution')))
        for _ in range(3):
            r.feed(broken)
        r.feed(dict(method='item/agentMessage/delta', params=dict(itemId='m', delta='still works')))
        await self.finish(r)
        events = lines(self.journal, self.worker)
        gaps = [e for e in events if e['kind'] == 'gap']
        self.assertEqual([g['data']['reason'] for g in gaps], ['normalizer-error'])
        self.assertIn('message.delta', kinds(events))

    async def test_an_unusable_directory_disables_the_recorder_without_raising(self):
        (self.journal.root / 'activity').write_text('in the way')
        r = self.recorder()
        r.begin('task-1', 1)
        r.emit('message.completed', dict(text='x', is_final_candidate=True))
        r.terminal('completed')
        self.assertFalse(await r.flush())
        self.assertTrue(r.dead)
        r.emit('message.completed', dict(text='ignored', is_final_candidate=True))
        await r.close()

    async def test_close_flushes_and_is_idempotent(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.terminal('completed', 'x')
        await r.close()
        await r.close()
        self.assertEqual(kinds(lines(self.journal, self.worker))[-1], 'task.terminal')
        self.assertIsNone(r.fd)

    async def test_hostile_values_cannot_break_the_log(self):
        r = self.recorder()
        r.begin('task-1', 1)
        r.emit('tool.started', dict(kind='mcp', title='t', input=dict(nan=float('nan'), inf=float('inf'), surrogate='\ud800 bad', deep={'a': {'b': {'c': {'d': {'e': {'f': {'g': {'h': {'i': 1}}}}}}}}}, big=list(range(1000)))), item='h')
        await self.finish(r)
        events = lines(self.journal, self.worker)
        data = next(e for e in events if e['kind'] == 'tool.started')['data']['input']
        self.assertIsNone(data['nan'])
        self.assertIsNone(data['inf'])
        self.assertEqual(len(data['big']), 256)

    async def test_worker_ids_cannot_escape_the_directory(self):
        for bad in ('../x', '/etc/passwd', '.hidden', ''):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Recorder(self.journal, bad, 'g', 'codex')


class SchemaTests(RecorderCase):
    def validator(self):
        return jsonschema.Draft202012Validator(SCHEMA)

    async def replay(self, provider, normalizer_events, **kwargs):
        r = self.recorder(provider)
        r.begin('task-1', 1, origin='parent-model', client_request_id='req', input_preview='go')
        for event in normalizer_events:
            r.feed(event)
        await self.finish(r)
        return lines(self.journal, self.worker)

    async def test_recorder_output_validates_for_every_fixture(self):
        fixtures = ROOT / 'tests/fixtures/activity'
        validator = self.validator()
        runs = [('codex', [json.loads(x) for x in (fixtures / 'codex' / (name + '.jsonl')).read_text().splitlines()]) for name in ('completed-turn', 'failed-turn', 'interrupted-turn')]
        runs.append(('claude', [json.loads(x) for x in (fixtures / 'claude/2.1.314-stream.jsonl').read_text().splitlines()]))
        for provider, events in runs:
            self.directory2 = tempfile.TemporaryDirectory()
            self.addCleanup(self.directory2.cleanup)
            self.journal2 = Journal(Path(self.directory2.name))
            self.addCleanup(self.journal2.close)
            recorder = Recorder(self.journal2, new_id(), 'g', provider, lease={'verified_runtime': {'sha256': SHA}}, cwd='/work/repo')
            recorder.begin('task-1', 1, input_preview='go')
            for event in events:
                recorder.feed(event)
            recorder.terminal('completed', 'x')
            await recorder.flush()
            written = lines(self.journal2, recorder.worker_id)
            self.assertGreater(len(written), 4)
            for event in written:
                with self.subTest(provider=provider, seq=event['seq'], kind=event['kind']):
                    self.assertEqual(list(validator.iter_errors(event)), [])

    async def test_specification_examples_validate(self):
        text = (ROOT / 'docs/CLAUDE_LINEAGE_MOD.md').read_text()
        examples = [json.loads(line) for line in text[text.index('## Appendix B'):text.index('## Appendix C')].splitlines() if line.startswith('{')]
        self.assertEqual(len(examples), 4)
        for example in examples:
            with self.subTest(kind=example['kind']):
                self.assertEqual(list(self.validator().iter_errors(example)), [])

    async def test_every_kind_has_a_data_definition_and_the_schema_rejects_bad_data(self):
        from nativepeers.activity.events import KINDS
        self.assertEqual(sorted(SCHEMA['$defs']['data']), sorted(KINDS))
        self.assertEqual(sorted(SCHEMA['properties']['kind']['enum']), sorted(KINDS))
        good = dict(schema='nativepeers.activity.v1', seq=1, at='2026-10-30T17:12:01.120Z', worker_id='W', group_id='G', task_id='T', generation=1,
                    native_turn=None, item=None, kind='task.terminal', data=dict(status='completed', final_text=''), source=dict(provider='codex', event=None))
        self.assertEqual(list(self.validator().iter_errors(good)), [])
        for mutation in (dict(data=dict(status='done', final_text='')), dict(seq=0), dict(kind='nope'), dict(schema='other'), dict(source=dict(provider='x', event=None))):
            with self.subTest(mutation=mutation):
                self.assertNotEqual(list(self.validator().iter_errors(dict(good, **mutation))), [])

    async def test_generic_lifecycle_events_validate_for_all_four_providers(self):
        from nativepeers.activity.context import finish_task
        validator = self.validator()
        for provider in ('codex', 'claude', 'antigravity', 'grok'):
            r = Recorder(self.journal, new_id(), 'g', provider)
            r.begin('t', 1, origin='controller')
            finish_task(r, provider, dict(status='completed', text='final', usage=dict(input_tokens=3, output_tokens=2, cache_read_input_tokens=1)), 'artifact-1')
            await r.flush()
            written = lines(self.journal, r.worker_id)
            for event in written:
                with self.subTest(provider=provider, kind=event['kind']):
                    self.assertEqual(list(validator.iter_errors(event)), [])
            self.assertEqual(kinds(written)[-1], 'task.terminal')
            self.assertEqual(written[-1]['data']['result_artifact_id'], 'artifact-1')
            generic = provider in ('antigravity', 'grok')
            self.assertEqual('message.completed' in kinds(written), generic)
            self.assertEqual('usage' in kinds(written), generic)


if __name__ == '__main__':
    unittest.main()
