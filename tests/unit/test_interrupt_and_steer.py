"""The controller's explicit interrupt versus task cancel, Codex steering, and task generations in the activity log."""
import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, Mock

from nativepeers.activity import Reader
from nativepeers.adapters.codex_app_server import ProviderError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id
from nativepeers.worker_process import Worker


class ControllerCase(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory();self.addCleanup(directory.cleanup)
        self.j = Journal(Path(directory.name));self.addCleanup(self.j.close)

    def fixture(self, provider='claude', followup=None):
        group, worker, first, second = [new_id() for _ in range(4)]
        policy = {'initial_tasks': [first], 'parent_incarnation': new_id(), 'phases': {'1': [first], '2': [second]}}
        lease = {'provider_home': str(self.j.root.resolve()), 'spec': {'provider': provider, 'model': 'sonnet', 'max_task_turns': 8, 'wall_time_seconds': 5},
                 'binding': str(self.j.root / 'bindings/test'), 'parent_key': 'parent'}
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (group, 'parent', canonical(policy).decode(), 'RUNNING', '{}'))
        self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                          (worker, group, 'peer', provider, new_id(), 'worker', 'sonnet', None, None, str(self.j.root), None, 'RUNNING', 1, canonical(lease).decode()))
        for task, body in ((first, {'initial': True}), (second, followup or {})):
            self.j.db.execute('INSERT INTO tasks VALUES(?,?,?,?,?,?,?,?,?)', (task, worker, None, None, 'QUEUED', None, None, 1, canonical(body).decode()))
        w = Worker(self.j, worker);w.emit = Mock()
        return w, first, second

    def events(self, w):
        return Reader(self.j.root, w.row['worker_id']).read(0, limit=512)['events']

    def record(self, task):
        row = self.j.one('SELECT t.state,a.relative_path FROM tasks t JOIN artifacts a ON a.artifact_id=t.result_artifact_id WHERE t.task_id=?', (task,))
        return row['state'], json.loads((self.j.root / 'artifacts' / row['relative_path']).read_text())

    async def stopped(self, interrupt):
        w, first, _ = self.fixture()
        started = asyncio.Event()

        async def turn(text):
            w.partial = 'half of the answer';started.set()
            await asyncio.Event().wait()

        w.claude_turn = turn
        running = asyncio.create_task(w.task(first, 'go'))
        await asyncio.wait_for(started.wait(), 2)
        await w.cancel_task(first, interrupt=interrupt)
        await asyncio.wait_for(running, 5)
        await w.activity.close()
        return w, first


class InterruptTests(ControllerCase):
    async def test_an_interrupt_ends_only_the_turn_with_its_partial_result_and_the_worker_stays_resumable(self):
        w, first = await self.stopped(interrupt=True)
        state, record = self.record(first)
        self.assertEqual((state, record['status'], record['summary'], record['error']['code']), ('INTERRUPTED', 'interrupted', 'half of the answer', 'USER_INTERRUPTED'))
        terminal = [e for e in self.events(w) if e['kind'] == 'task.terminal'][-1]
        self.assertEqual((terminal['data']['status'], terminal['data']['error']['code'], terminal['data']['final_text']), ('interrupted', 'USER_INTERRUPTED', 'half of the answer'))
        self.assertEqual(self.j.one('SELECT state FROM workers')['state'], 'IDLE_GRACE')
        self.assertFalse(w.interrupting or w.retire_after_task or w.stopping)

    async def test_a_cancel_ends_the_task_canceled(self):
        w, first = await self.stopped(interrupt=False)
        state, record = self.record(first)
        self.assertEqual((state, record['error']['code']), ('CANCELED', 'USER_CANCELED'))
        self.assertEqual([e for e in self.events(w) if e['kind'] == 'task.terminal'][-1]['data']['status'], 'canceled')

    async def test_a_cancel_after_an_interrupt_request_wins(self):
        w, first, _ = self.fixture()
        started = asyncio.Event()
        async def turn(text):
            started.set();await asyncio.Event().wait()
        w.claude_turn = turn
        running = asyncio.create_task(w.task(first, 'go'))
        await started.wait()
        w.canceling_task = first;w.interrupting = True
        await w.cancel_task(first)
        await asyncio.wait_for(running, 5)
        self.assertEqual(self.record(first)[0], 'CANCELED')

    async def test_an_interrupt_that_arrives_after_completion_leaves_the_result_completed(self):
        w, first, _ = self.fixture()
        w.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'done', 'native_turn_ids': []})
        await w.task(first, 'go')
        await w.cancel_task(first, interrupt=True)
        self.assertEqual(self.record(first)[0], 'COMPLETED');self.assertFalse(w.interrupting)


class GenerationTests(ControllerCase):
    async def test_a_follow_up_in_the_same_controller_is_the_next_task_generation_with_its_provenance(self):
        w, first, second = self.fixture(followup={'origin': 'human-claude-view', 'client_request_id': 'np-in:row-1'})
        w.claude_turn = AsyncMock(return_value={'status': 'completed', 'text': 'ok', 'native_turn_ids': []})
        await w.task(first, 'go');await w.task(second, 'more')
        await w.activity.close()
        started = [e for e in self.events(w) if e['kind'] == 'task.started']
        self.assertEqual([(e['task_id'], e['generation']) for e in started], [(first, 1), (second, 2)])
        self.assertEqual((started[1]['data']['origin'], started[1]['data']['client_request_id']), ('human', 'np-in:row-1'))
        self.assertEqual([r['generation'] for r in self.j.all('SELECT generation FROM activity_index ORDER BY first_seq')], [1, 2])


class SteerTests(ControllerCase):
    async def steer(self, call):
        w, first, _ = self.fixture('codex')
        w.client = Mock(call=call);w.running_task = first;w.turn = 'turn-1';w.native = 'thread-1'
        w.activity.begin(first, 1)
        await w.steer(dict(task_id=first, request_id='r1', text='also this', input=dict(client_request_id='np-in:s', origin='parent-model', mode='steer', preview='also this')))
        await w.activity.close()
        return w, w.emit.call_args

    async def test_a_steer_calls_turn_steer_with_the_installed_schemas_fields(self):
        call = AsyncMock(return_value={'turnId': 'turn-1'})
        w, emitted = await self.steer(call)
        call.assert_awaited_once_with('turn/steer', {'threadId': 'thread-1', 'expectedTurnId': 'turn-1', 'input': [{'type': 'text', 'text': 'also this'}]}, timeout=5)
        self.assertEqual((emitted.args[0], emitted.kwargs['steered'], emitted.kwargs['request_id']), ('steered', True, 'r1'))
        queued = [e for e in self.events(w) if e['kind'] == 'input.queued']
        self.assertEqual(queued[0]['data']['mode'], 'steer')

    async def test_a_refused_steer_is_definite_and_a_lost_one_is_uncertain(self):
        _, refused = await self.steer(AsyncMock(side_effect=ProviderError('turn/steer', {'code': -32600, 'message': 'no active turn'})))
        self.assertEqual((refused.kwargs['steered'], refused.kwargs['error']['code'], refused.kwargs.get('uncertain')), (False, 'STEER_REFUSED', None))
        _, lost = await self.steer(AsyncMock(side_effect=TimeoutError()))
        self.assertEqual((lost.kwargs['steered'], lost.kwargs['uncertain']), (False, True))


if __name__ == '__main__':
    unittest.main()
