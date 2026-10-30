import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import jsonschema

from nativepeers.activity.claude import Claude
from nativepeers.activity.codex import Codex
from nativepeers.activity.events import KINDS

FIXTURES = Path(__file__).resolve().parents[1] / 'fixtures' / 'activity'
CODEX_TURNS = {'completed-turn': '0198f2a0-2222-7000-8000-000000000002', 'failed-turn': '0198f2a0-3333-7000-8000-000000000003',
               'interrupted-turn': '0198f2a0-4444-7000-8000-000000000004'}
SCHEMA_DIGEST = 'a1717fb10554d85204ca8d7fc84e6ce4214c5191b37f1a36771d5b315614e6f8'
CODEX = Path('/Users/danny/.local/share/codex-mcp-manager/releases/rust-v0.164.0-e147f02ecb40-1793009714936778000/bin/codex')
CODEX_SHA = '3998a0e51bb7e4a2c8da905e9ba2f9e149c81ae44c487de59cf1e7296c04dadc'


def notifications(name):
    return [json.loads(line) for line in (FIXTURES / 'codex' / (name + '.jsonl')).read_text().splitlines()]


def run(normalizer, events):
    out = [dict(kind=e.kind, item=e.item, event=e.event, data=e.data) for event in events for e in normalizer.feed(event)]
    return dict(native_status=normalizer.native_status, terminal_event=normalizer.terminal_event, emissions=out)


def codex_run(name):
    normalizer = Codex(cwd='/work/repo')
    normalizer.turn = CODEX_TURNS[name]
    return run(normalizer, notifications(name))


class CodexNormalizerTests(unittest.TestCase):
    def test_fixtures_conform_to_the_schema_generated_by_the_leased_runtime(self):
        schema = json.loads((FIXTURES / 'codex/schema/ServerNotification.json').read_text())
        validator = jsonschema.validators.validator_for(schema)(schema)
        for name in CODEX_TURNS:
            for index, notification in enumerate(notifications(name)):
                with self.subTest(name=name, index=index, method=notification['method']):
                    self.assertEqual(list(validator.iter_errors(notification)), [])

    def test_recorded_schema_file_has_the_recorded_digest(self):
        self.assertEqual(hashlib.sha256((FIXTURES / 'codex/schema/ServerNotification.json').read_bytes()).hexdigest(), SCHEMA_DIGEST)

    @unittest.skipUnless(CODEX.is_file() and hashlib.sha256(CODEX.read_bytes()).hexdigest() == CODEX_SHA, 'the leased Codex runtime is not installed')
    def test_generated_schema_matches_the_recorded_digest(self):
        with tempfile.TemporaryDirectory() as home, tempfile.TemporaryDirectory() as out:
            subprocess.run([str(CODEX), 'app-server', 'generate-json-schema', '--experimental', '--out', out], check=True, capture_output=True,
                           env={'CODEX_HOME': home, 'PATH': '/usr/bin:/bin'}, timeout=60)
            self.assertEqual(hashlib.sha256((Path(out) / 'ServerNotification.json').read_bytes()).hexdigest(), SCHEMA_DIGEST)
            requests = json.loads((Path(out) / 'ClientRequest.json').read_text())
            self.assertIn('turn/steer', [v['properties']['method']['enum'][0] for v in requests['oneOf']])

    def test_golden_output_per_fixture(self):
        for name in CODEX_TURNS:
            with self.subTest(name=name):
                golden = json.loads((FIXTURES / 'codex' / (name + '.golden.json')).read_text())
                self.assertEqual(codex_run(name), golden)

    def test_item_families_map_to_the_specified_kinds(self):
        result = codex_run('completed-turn')['emissions']
        started = {e['item']: e['data']['kind'] for e in result if e['kind'] == 'tool.started'}
        self.assertEqual(started, {'cmd_1': 'exec', 'fc_1': 'edit', 'mcp_1': 'mcp', 'ws_1': 'web'})
        changed = next(e for e in result if e['kind'] == 'files.changed')
        self.assertEqual(changed['data']['paths'], ['src/pipeline.py', 'tests/test_new.py'])
        self.assertEqual(changed['data']['stats'], dict(added=7, removed=1))
        self.assertEqual(next(e for e in result if e['kind'] == 'plan.updated')['data']['items'][1], dict(text='Patch', status='in_progress'))
        finals = [e['data']['is_final_candidate'] for e in result if e['kind'] == 'message.completed']
        self.assertEqual(finals, [False, True])

    def test_every_started_tool_has_exactly_one_completion_unless_interrupted(self):
        for name, open_items in (('completed-turn', set()), ('failed-turn', set()), ('interrupted-turn', {'cmd_4', 'fc_2'})):
            with self.subTest(name=name):
                emissions = codex_run(name)['emissions']
                started = [e['item'] for e in emissions if e['kind'] == 'tool.started']
                completed = [e['item'] for e in emissions if e['kind'] == 'tool.completed']
                self.assertEqual(set(started) - set(completed), open_items)
                self.assertEqual(len(completed), len(set(completed)))

    def test_raw_reasoning_is_never_recorded(self):
        text = json.dumps(codex_run('completed-turn'))
        self.assertNotIn('raw chain of thought', text)
        deltas = [e['data']['text'] for e in codex_run('completed-turn')['emissions'] if e['kind'] == 'reasoning.delta']
        self.assertEqual(''.join(deltas), 'Looking at the test layout.\n\nPlanning the fix.')

    def test_usage_is_cumulative_from_the_first_update_of_the_task(self):
        usage = [e['data'] for e in codex_run('completed-turn')['emissions'] if e['kind'] == 'usage']
        self.assertEqual(usage[0], dict(input_tokens=1000, output_tokens=50, cached_input_tokens=400, reasoning_tokens=20, cumulative=True))
        self.assertEqual(usage[1], dict(input_tokens=2600, output_tokens=180, cached_input_tokens=1800, reasoning_tokens=60, cumulative=True))

    def test_status_flags_become_task_states_once(self):
        states = [e['data'] for e in codex_run('failed-turn')['emissions'] if e['kind'] == 'task.state']
        self.assertEqual(states, [dict(state='APPROVAL_BLOCKED', reason='waitingOnApproval'), dict(state='RUNNING')])

    def test_failure_statuses(self):
        done = {e['item']: e['data']['status'] for e in codex_run('failed-turn')['emissions'] if e['kind'] == 'tool.completed'}
        self.assertEqual(done, {'cmd_2': 'error', 'cmd_3': 'declined', 'mcp_2': 'error'})
        self.assertEqual(codex_run('failed-turn')['native_status'], 'failed')
        self.assertEqual(codex_run('interrupted-turn')['native_status'], 'interrupted')

    def test_other_turns_and_unknown_items(self):
        normalizer = Codex()
        normalizer.turn = 'mine'
        theirs = dict(method='item/started', params=dict(threadId='t', turnId='other', item=dict(type='commandExecution', id='x', command='ls'), startedAtMs=1))
        self.assertEqual(normalizer.feed(theirs), [])
        odd = dict(method='item/started', params=dict(threadId='t', turnId='mine', item=dict(type='hologram', id='h1'), startedAtMs=1))
        first, second = normalizer.feed(odd), normalizer.feed(dict(odd, params=dict(odd['params'], item=dict(type='hologram', id='h2'))))
        self.assertEqual([e.kind for e in first], ['gap'])
        self.assertEqual(first[0].data, dict(reason='unmapped', event='item:hologram'))
        self.assertEqual(second, [])


class ClaudeNormalizerTests(unittest.TestCase):
    def stream(self):
        return [json.loads(line) for line in (FIXTURES / 'claude/2.1.314-stream.jsonl').read_text().splitlines()]

    def test_golden_output_for_the_recorded_stream(self):
        golden = json.loads((FIXTURES / 'claude/2.1.314-stream.golden.json').read_text())
        self.assertEqual(run(Claude(), self.stream()), golden)

    def test_partial_message_recording_matches_the_plain_one(self):
        partial = [json.loads(line) for line in (FIXTURES / 'claude/2.1.314-stream-partial.jsonl').read_text().splitlines()]
        plain = run(Claude(), self.stream())['emissions']
        self.assertEqual([(e['kind'], e['data'].get('status')) for e in run(Claude(), partial)['emissions']], [(e['kind'], e['data'].get('status')) for e in plain])

    def test_tool_kinds_and_titles(self):
        emissions = run(Claude(), self.stream())['emissions']
        started = [(e['data']['kind'], e['data']['title']) for e in emissions if e['kind'] == 'tool.started']
        self.assertEqual(started, [('exec', 'Print a greeting'), ('edit', 'notes.txt'), ('edit', 'notes.txt'), ('read', 'notes.txt'),
                                   ('plan', 'Update todo list'), ('exec', 'A failing command'), ('read', 'missing.txt')])

    def test_results_pair_by_tool_use_id_with_status_and_exit_code(self):
        emissions = run(Claude(), self.stream())['emissions']
        completed = {e['item']: e['data'] for e in emissions if e['kind'] == 'tool.completed'}
        started = [e['item'] for e in emissions if e['kind'] == 'tool.started']
        self.assertEqual(sorted(completed), sorted(started))
        statuses = [(completed[item]['status'], completed[item].get('exit_code')) for item in started]
        self.assertEqual(statuses, [('ok', 0), ('ok', None), ('ok', None), ('ok', None), ('error', None), ('error', 3), ('error', None)])

    def test_edit_diffs_come_from_the_structured_patch(self):
        changes = [e['data'] for e in run(Claude(), self.stream())['emissions'] if e['kind'] == 'files.changed']
        self.assertEqual([c['paths'] for c in changes], [['notes.txt'], ['notes.txt']])
        self.assertEqual(changes[0]['stats'], dict(added=2, removed=0))
        self.assertEqual(changes[1], dict(paths=['notes.txt'], stats=dict(added=1, removed=1), diff=dict(text='@@ -1,2 +1,2 @@\n one\n-two\n+three')))

    def test_a_refused_todo_write_does_not_update_the_plan(self):
        self.assertEqual([e for e in run(Claude(), self.stream())['emissions'] if e['kind'] == 'plan.updated'], [])

    def test_todo_write_updates_the_plan_when_it_succeeds(self):
        normalizer = Claude()
        todos = [dict(content='Check notes', status='in_progress', activeForm='Checking'), dict(content='Report', status='pending', activeForm='Reporting')]
        use = dict(type='assistant', message=dict(id='m', content=[dict(type='tool_use', id='t1', name='TodoWrite', input=dict(todos=todos))]))
        done = dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='t1', content='ok')]))
        out = [e for event in (use, done) for e in normalizer.feed(event)]
        self.assertEqual(out[-1].data, dict(items=[dict(text='Check notes', status='in_progress'), dict(text='Report', status='pending')]))

    def test_tool_names_are_compared_as_strings(self):
        normalizer = Claude()
        names = ['Glob', 'Grep', 'MultiEdit', 'mcp__docs__lookup', 'WebFetch', 'Agent', 'SomethingNew']
        events = [dict(type='assistant', message=dict(id='m', content=[dict(type='tool_use', id='t%d' % i, name=name, input={})])) for i, name in enumerate(names)]
        kinds = [normalizer.feed(event)[0].data['kind'] for event in events]
        self.assertEqual(kinds, ['search', 'search', 'edit', 'mcp', 'web', 'agent', 'other'])

    def test_permission_denial_and_interruption(self):
        normalizer = Claude()
        normalizer.feed(dict(type='assistant', message=dict(id='m', content=[dict(type='tool_use', id='a', name='Bash', input=dict(command='rm x')),
                                                                         dict(type='tool_use', id='b', name='Bash', input=dict(command='sleep 9'))])))
        denied = dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='a', content='denied', is_error=True)]),
                      tool_result_meta=[dict(id='a', permission_decision=dict(decision='deny'))])
        stopped = dict(type='user', message=dict(content=[dict(type='tool_result', tool_use_id='b', content='')]), tool_use_result=dict(interrupted=True))
        self.assertEqual(normalizer.feed(denied)[0].data['status'], 'declined')
        self.assertEqual(normalizer.feed(stopped)[0].data['status'], 'interrupted')

    def test_usage_is_emitted_once_per_message_and_only_when_nonzero(self):
        normalizer = Claude()
        usage = dict(input_tokens=10, output_tokens=5, cache_read_input_tokens=3, output_tokens_details=dict(thinking_tokens=2))
        event = dict(type='assistant', message=dict(id='m1', usage=usage, content=[dict(type='text', text='a'), ]))
        again = dict(type='assistant', message=dict(id='m1', usage=usage, content=[dict(type='text', text='b')]))
        first = [e for e in normalizer.feed(event) if e.kind == 'usage']
        second = [e for e in normalizer.feed(again) if e.kind == 'usage']
        self.assertEqual(first[0].data, dict(input_tokens=10, output_tokens=5, cached_input_tokens=3, reasoning_tokens=2, cumulative=False))
        self.assertEqual(second, [])

    def test_thinking_blocks_and_unmapped_blocks(self):
        normalizer = Claude()
        event = dict(type='assistant', message=dict(id='m', content=[dict(type='thinking', thinking='weighing options'), dict(type='mystery'), dict(type='mystery')]))
        out = normalizer.feed(event)
        self.assertEqual([(e.kind, e.data) for e in out], [('reasoning.delta', dict(text='weighing options')), ('gap', dict(reason='unmapped', event='assistant/mystery'))])

    def test_kinds_are_the_specified_vocabulary(self):
        self.assertEqual(len(KINDS), 16)
        emitted = {e['kind'] for e in run(Claude(), self.stream())['emissions']} | {e['kind'] for n in CODEX_TURNS for e in codex_run(n)['emissions']}
        self.assertLessEqual(emitted, set(KINDS))


if __name__ == '__main__':
    unittest.main()
