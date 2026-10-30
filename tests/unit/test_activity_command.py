import asyncio
import io
import json
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import jsonschema
from referencing import Registry, Resource

from nativepeers.activity import Recorder
from nativepeers.caller import Caller
from nativepeers.cli import main, parser
from nativepeers.errors import PeerError
from nativepeers.files import canonical
from nativepeers.journal import Journal
from nativepeers.models import new_id

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / 'schemas/activity.v1.json').read_text())
PAGE = jsonschema.Draft202012Validator({'$ref': SCHEMA['$id'] + '#/$defs/page'}, registry=Registry().with_resource(SCHEMA['$id'], Resource.from_contents(SCHEMA)))


class Keep:
    """Hands main() the test's journal without letting it close it."""
    def __init__(self, journal):
        self.journal = journal

    def __getattr__(self, name):
        return getattr(self.journal, name)

    def close(self):
        pass


class ActivityCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.j = Journal(Path(self.directory.name))
        self.addCleanup(self.j.close)
        self.group, self.worker, self.sibling = new_id(), new_id(), new_id()
        self.j.db.execute('INSERT INTO groups VALUES(?,?,?,?,?)', (self.group, 'parent-key', '{}', 'RUNNING', '{}'))
        for worker in (self.worker, self.sibling):
            self.j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                              (worker, self.group, 'peer', 'codex', new_id(), 'worker', 'm', None, None, str(self.j.root), None, 'RUNNING', 1, canonical({}).decode()))

    def record(self, worker=None, count=6):
        asyncio.run(self.write(worker, count))

    async def write(self, worker, count):
        recorder = Recorder(self.j, worker or self.worker, self.group, 'codex')
        recorder.begin('task-1', 1)
        for number in range(count):
            recorder.emit('tool.started', dict(kind='exec', title='c%d' % number, input={}), item='item-%d' % (number % 3))
            recorder.emit('tool.completed', dict(status='ok', output='out'), item='item-%d' % (number % 3))
        recorder.terminal('completed', 'finished', result_artifact_id='art-1')
        await recorder.close()

    def call(self, *argv, caller=None, error=None):
        output = io.StringIO()
        binding = patch('nativepeers.cli.bind_caller', side_effect=error) if error else patch('nativepeers.cli.bind_caller', return_value=caller)
        with patch('sys.argv', ['npeer', 'activity', *argv, '--json']), patch('nativepeers.cli.Journal', return_value=Keep(self.j)), binding, patch('sys.stdout', output):
            with self.assertRaises(SystemExit) as raised:
                main()
        return raised.exception.code, json.loads(output.getvalue())

    def parent(self):
        return Caller('peer', 'parent-key', 'incarnation', {})

    def test_the_parent_reads_a_page_in_the_standard_envelope(self):
        self.record()
        code, response = self.call(self.worker, '--after', '0', caller=self.parent())
        self.assertEqual(code, 0)
        self.assertEqual(sorted(response), ['data', 'error', 'ok', 'request_id', 'schema_version'])
        self.assertEqual((response['schema_version'], response['ok'], response['error']), (1, True, None))
        data = response['data']
        self.assertEqual([e['seq'] for e in data['events']], list(range(1, 16)))
        self.assertEqual((data['next_seq'], data['last_seq'], data['more'], data['gap'], data['worker_id']), (15, 15, False, None, self.worker))
        self.assertEqual(data['tasks'], [dict(generation=1, task_id='task-1', first_seq=1, last_seq=15, terminal_seq=15)])
        self.assertEqual(list(PAGE.iter_errors(data)), [])

    def test_after_max_and_item_select_the_page(self):
        self.record()
        _, response = self.call(self.worker, '--after', '4', '--max', '3', caller=self.parent())
        self.assertEqual(([e['seq'] for e in response['data']['events']], response['data']['more'], response['data']['next_seq']), ([5, 6, 7], True, 7))
        _, response = self.call(self.worker, '--item', 'item-1', caller=self.parent())
        self.assertEqual({e['item'] for e in response['data']['events']}, {'item-1'})
        self.assertEqual(len(response['data']['events']), 4)

    def test_wait_returns_after_the_bound_when_nothing_arrives(self):
        self.record()
        start = time.monotonic()
        code, response = self.call(self.worker, '--after', '15', '--wait-ms', '300', caller=self.parent())
        self.assertEqual(code, 0)
        self.assertTrue(response['data']['timed_out'] and response['data']['events'] == [])
        self.assertGreaterEqual(time.monotonic() - start, 0.28)

    def test_an_unbound_caller_is_refused(self):
        self.record()
        code, response = self.call(self.worker, error=PeerError('CALLER_UNBOUND', 'no verified native caller'))
        self.assertEqual((code, response['ok'], response['data'], response['error']['code']), (4, False, None, 'CALLER_UNBOUND'))

    def test_a_peer_outside_the_group_is_refused(self):
        self.record()
        code, response = self.call(self.worker, caller=Caller('peer', 'someone-else', 'incarnation', {}))
        self.assertEqual((code, response['ok'], response['error']['code']), (4, False, 'APPROVAL_BLOCKED'))
        code, response = self.call(self.worker, caller=Caller('external-local', 'external-local'))
        self.assertEqual((code, response['error']['code']), (4, 'APPROVAL_BLOCKED'))

    def test_a_worker_reads_only_its_own_activity(self):
        self.record()
        self.record(self.sibling)
        mine = Caller('peer', 'worker-key', 'incarnation', {'worker_id': self.worker, 'group_id': self.group})
        code, response = self.call(self.worker, caller=mine)
        self.assertEqual((code, response['ok']), (0, True))
        code, response = self.call(self.sibling, caller=mine)
        self.assertEqual((code, response['error']['code']), (4, 'APPROVAL_BLOCKED'))
        other_group = Caller('peer', 'worker-key', 'incarnation', {'worker_id': self.worker, 'group_id': new_id()})
        self.assertEqual(self.call(self.worker, caller=other_group)[1]['error']['code'], 'APPROVAL_BLOCKED')

    def test_unknown_workers_and_bad_arguments_are_protocol_errors(self):
        self.record()
        self.assertEqual(self.call(new_id(), caller=self.parent())[1]['error']['code'], 'PROTOCOL_ERROR')
        self.assertEqual(self.call(self.worker, '--after', '-1', caller=self.parent())[1]['error']['code'], 'PROTOCOL_ERROR')
        self.assertEqual(self.call(self.worker, '--max', '0', caller=self.parent())[1]['error']['code'], 'PROTOCOL_ERROR')
        self.assertEqual(self.call('../../etc/passwd', caller=self.parent())[1]['error']['code'], 'PROTOCOL_ERROR')

    def test_a_worker_without_a_log_reads_as_empty(self):
        code, response = self.call(self.worker, caller=self.parent())
        self.assertEqual((code, response['data']['events'], response['data']['last_seq'], response['data']['tasks']), (0, [], None, []))

    def test_the_command_is_registered(self):
        args = parser().parse_args(['activity', 'W', '--after', '5', '--wait-ms', '100', '--item', 'i', '--max', '9', '--json'])
        self.assertEqual((args.worker_id, args.after, args.wait_ms, args.item, args.max), ('W', 5, 100, 'i', 9))


if __name__ == '__main__':
    unittest.main()
