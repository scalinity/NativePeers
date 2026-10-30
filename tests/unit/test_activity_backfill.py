"""Backfill from native histories: read-only, same shapes as recorded activity."""
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace

from nativepeers.activity.backfill import backfill, claude_emissions, codex_emissions, history
from nativepeers.activity.claude import Claude

FIXTURES = Path(__file__).resolve().parents[1]/'fixtures/activity'


class Backfill(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_a_claude_transcript_maps_like_the_equivalent_stream(self):
        stream = [json.loads(l) for l in (FIXTURES/'claude/2.1.314-stream.jsonl').read_text().splitlines()]
        transcript = self.tmp/'t.jsonl'
        cwd = next(e['cwd'] for e in stream if e.get('type') == 'system')
        with transcript.open('w') as out:
            for event in stream:
                if event.get('type') not in ('assistant', 'user'):continue
                row = {k: v for k, v in event.items() if k != 'tool_use_result'}
                if 'tool_use_result' in event:row['toolUseResult'] = event['tool_use_result']
                out.write(json.dumps(dict(row, isSidechain=False, sessionId='s', cwd=cwd))+'\n')
            out.write(json.dumps(dict(type='assistant', isSidechain=True, agentId='sub', message=dict(id='x', content=[dict(type='text', text='subagent row')])))+'\n')
            out.write('not json\n')
        normalizer = Claude()
        expected = [e for event in stream for e in normalizer.feed(event)]
        self.assertEqual(list(claude_emissions(transcript)), expected)

    def test_a_codex_rollout_maps_messages_commands_file_changes_and_usage(self):
        rollout = self.tmp/'rollout.jsonl'
        rows = [dict(type='session_meta', payload=dict(id='th')),
                dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='CommandExecution', id='c1', command=['bash', '-lc', 'pytest -q'], cwd='/w',
                     status='completed', aggregated_output='2 passed', exit_code=0, duration=dict(secs=1, nanos=500000000)))),
                dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='FileChange', id='f1', changes={'/w/a.py': dict(type='update', unified_diff='@@\n-a\n+b')}, status='completed'))),
                dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='Reasoning', id='r1', summary_text=['x']))),
                dict(type='event_msg', payload=dict(type='token_count', info=dict(total_token_usage=dict(input_tokens=10, output_tokens=5, cached_input_tokens=2, reasoning_output_tokens=1)))),
                dict(type='event_msg', payload=dict(type='item_completed', item=dict(type='AgentMessage', id='m1', phase='final_answer', content=[dict(type='Text', text='Done.')])))]
        rollout.write_text('\n'.join(json.dumps(r) for r in rows)+'\n')
        out = [(e.kind, e.item) for e in codex_emissions(rollout, '/w')]
        self.assertEqual(out, [('tool.started', 'c1'), ('tool.completed', 'c1'), ('tool.started', 'f1'), ('tool.completed', 'f1'), ('files.changed', 'f1'),
                               ('message.completed', 'm1'), ('usage', None)])
        emissions = list(codex_emissions(rollout, '/w'))
        self.assertEqual(emissions[1].data, dict(status='ok', output='2 passed', exit_code=0, duration_ms=1500))
        self.assertEqual(emissions[4].data['paths'], ['a.py']);self.assertEqual(emissions[4].data['stats'], dict(added=1, removed=1))
        self.assertEqual(emissions[6].data, dict(input_tokens=10, output_tokens=5, cached_input_tokens=2, reasoning_tokens=1, cumulative=True))

    def test_backfill_frames_events_from_seq_one_and_marks_their_source(self):
        home = self.tmp/'claude';(home/'projects/p').mkdir(parents=True)
        (home/'projects/p/N1.jsonl').write_text(json.dumps(dict(type='assistant', message=dict(id='m', stop_reason='end_turn', content=[dict(type='text', text='All done.')])))+'\n')
        journal = SimpleNamespace(root=self.tmp/'state', one=lambda sql, args: dict(task_id='T', generation=2, state='COMPLETED'))
        row = dict(provider='claude', worker_id='W', group_id='G', native_id='N1', generation=2, cwd='/w')
        events = backfill(journal, row, home)
        self.assertEqual([e['seq'] for e in events], list(range(1, len(events)+1)))
        self.assertEqual([e['kind'] for e in events], ['task.started', 'message.completed', 'task.terminal'])
        self.assertTrue(all(e['source']['event'] == 'backfill' and e['generation'] == 2 for e in events))
        self.assertEqual(events[-1]['data'], dict(status='completed', final_text='All done.'))
        self.assertEqual(backfill(journal, dict(row, provider='antigravity'), home)[1]['data'], dict(reason='no-history-reader'))
        self.assertIsNone(history('claude', home, '../etc'))


if __name__ == '__main__':
    unittest.main()
