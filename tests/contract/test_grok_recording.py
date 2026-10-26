"""Parse retained native recordings independently of the subprocess doubles."""
import json
from pathlib import Path

from nativepeers.grok_worker import Stream,parse_catalog


FIXTURES=Path(__file__).resolve().parents[1]/'fixtures/grok'


def test_recorded_native_stream_retains_completion_and_engine_usage():
    events=[json.loads(line) for line in (FIXTURES/'1.0.70-stream.jsonl').read_text().splitlines()]
    native=events[-1]['sessionId'];stream=Stream(native,'analysis')
    for event in events:stream.observe(event)
    result=stream.result(0)
    assert result['status']=='completed' and result['text']=='GROK_STREAM_OK'
    assert result['native_request_id']=='67e2b630-0116-4ce5-89b4-56b56933e6b7'
    assert result['native_internal_rounds']==1
    assert result['usage']['total_tokens']==23254
    assert result['model_usage']['grok-4.7-build']['modelCalls']==1
    assert set(stream.tools)=={'read_file','list_dir','grep'}


def test_recorded_native_catalog_retains_exact_selections():
    result=parse_catalog((FIXTURES/'1.0.70-models.txt').read_text())
    assert result['default']=='grok-4.7'
    assert list(result['models'])==['grok-4.7','grok-4.7-build-fast','grok-4.6','grok-4.5']
