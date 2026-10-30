"""Grok and Antigravity normalizers against recorded and documented streams."""
import json
from pathlib import Path
import unittest

from nativepeers.activity.antigravity import Antigravity
from nativepeers.activity.grok import Grok

FIXTURES = Path(__file__).resolve().parents[1]/'fixtures'


def run(normalizer, events):
    return [(e.kind, e.item, e.data) for event in events for e in normalizer.feed(event)]


class GrokStream(unittest.TestCase):
    def test_recorded_stream_streams_text_and_turn_usage(self):
        g = Grok('/disposable-fixture')
        out = run(g, [json.loads(line) for line in (FIXTURES/'grok/1.0.70-stream.jsonl').read_text().splitlines()])
        self.assertEqual(''.join(d['text'] for k, _, d in out if k == 'message.delta'), 'GROK_STREAM_OK')
        self.assertEqual([d for k, _, d in out if k == 'usage'], [dict(input_tokens=20142, output_tokens=40, cached_input_tokens=3072, reasoning_tokens=34, cumulative=True)])
        self.assertEqual({k for k, _, _ in out}, {'message.delta', 'usage'})
        self.assertEqual((g.native_status, g.terminal_event), ('end_turn', 'end'))

    def test_tool_calls_pair_by_id_with_kinds_and_file_changes(self):
        g = Grok('/w')
        out = run(g, [
            {'type': 'tool_call', 'toolName': 'write', 'toolCallId': 'write_1', 'kind': 'edit', 'status': 'in_progress', 'rawInput': {'path': '/w/a.txt', 'content': 'before'}},
            {'type': 'tool_call_update', 'toolCallId': 'write_1', 'status': 'completed', 'rawOutput': {'written': True}},
            {'type': 'tool_call', 'toolName': 'search_replace', 'toolCallId': 'edit_1', 'kind': 'edit', 'status': 'in_progress', 'rawInput': {'path': '/w/a.txt', 'old_string': 'before', 'new_string': 'after'}},
            {'type': 'tool_call_update', 'toolCallId': 'edit_1', 'status': 'completed', 'rawOutput': {'replaced': True}},
            {'type': 'tool_call', 'toolName': 'codegraph__codegraph_explore', 'toolCallId': 'call_1', 'kind': 'other', 'status': 'in_progress', 'rawInput': {}},
            {'type': 'tool_call_update', 'toolCallId': 'call_1', 'status': 'failed', 'rawOutput': 'denied'},
            {'type': 'tool_call', 'toolName': 'grep', 'toolCallId': 'g1', 'kind': 'search', 'status': 'completed', 'rawInput': {'pattern': 'TODO'}, 'rawOutput': '3 matches'},
            {'type': 'mystery'}, {'type': 'mystery'}])
        kinds = [(k, i) for k, i, _ in out]
        self.assertEqual(kinds, [('tool.started', 'write_1'), ('tool.completed', 'write_1'), ('files.changed', 'write_1'), ('tool.started', 'edit_1'),
                                 ('tool.completed', 'edit_1'), ('files.changed', 'edit_1'), ('tool.started', 'call_1'), ('tool.completed', 'call_1'),
                                 ('tool.started', 'g1'), ('tool.completed', 'g1'), ('gap', None)])
        self.assertEqual(out[0][2]['kind'], 'edit');self.assertEqual(out[0][2]['title'], 'a.txt')
        self.assertEqual(out[5][2]['diff']['text'], '@@\n-before\n+after');self.assertEqual(out[5][2]['stats'], dict(added=1, removed=1))
        self.assertEqual(out[6][2]['kind'], 'mcp');self.assertEqual(out[7][2]['status'], 'error')
        self.assertEqual(out[9][2], dict(status='ok', output='3 matches'))


class AntigravityStream(unittest.TestCase):
    def test_documented_stream(self):
        a = Antigravity()
        out = run(a, [json.loads(line) for line in (FIXTURES/'activity/antigravity/documented-stream.jsonl').read_text().splitlines()])
        self.assertEqual([k for k, _, _ in out], ['message.delta', 'usage', 'usage', 'tool.started', 'tool.completed'])
        self.assertTrue(out[0][2]['text'].startswith('Git rebase'))
        self.assertEqual(out[1][2], dict(input_tokens=10302, output_tokens=582, reasoning_tokens=551, cached_input_tokens=8113, cumulative=False))
        self.assertEqual(out[3][2], dict(kind='exec', title='echo hello_headless_demo', input={'CommandLine': 'echo hello_headless_demo'}))
        self.assertEqual(out[4][2], dict(status='ok', output='hello_headless_demo\r\n', duration_ms=70))
        self.assertEqual((a.native_status, a.terminal_event, a.cwd), ('SUCCESS', 'result', '/home/user/project'))

    def test_tool_steps_start_once_and_unknown_steps_gap_once(self):
        a = Antigravity('/w')
        step = lambda state, **kw: {'event': 'step_update', 'step_update': dict(step_index=7, state=state, step_type='tool', tool_name='write_to_file', **kw)}
        out = run(a, [step('ACTIVE', tool_info={'parameters': {'TargetFile': '/w/x.py'}}), step('ACTIVE'),
                      step('DONE', tool_info={'name': 'write_to_file', 'parameters': {'TargetFile': '/w/x.py'}, 'output': 'ok'}),
                      {'event': 'step_update', 'step_update': {'step_index': 8, 'state': 'DONE', 'step_type': 'planner'}},
                      {'event': 'step_update', 'step_update': {'step_index': 9, 'state': 'DONE', 'step_type': 'planner'}}, {'event': 'other'}])
        self.assertEqual([(k, i) for k, i, _ in out], [('tool.started', 'step7'), ('tool.completed', 'step7'), ('files.changed', 'step7'), ('gap', None), ('gap', None)])
        self.assertEqual(out[0][2]['kind'], 'edit');self.assertEqual(out[0][2]['title'], 'x.py')
        self.assertEqual(out[2][2]['paths'], ['x.py'])


if __name__ == '__main__':
    unittest.main()
