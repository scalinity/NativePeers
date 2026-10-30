from nativepeers.claude_stream import Evidence
from nativepeers.grok_worker import Stream
from nativepeers.models import new_id
from nativepeers.presentation import completion


def test_both_native_budget_causes_and_unavailable_counts_are_rendered_with_phase():
    for count in (None, 1):
        terminal={'subtype':'error_max_turns','is_error':True}
        if count is not None:terminal['num_turns']=count
        claude=Evidence().failure(terminal,1,native_max_turns=1)
        stream=Stream(new_id(),'analysis',native_max_turns=1)
        stream.observe({'type':'end','stopReason':'max_turns',**({'num_turns':count} if count is not None else {})})
        for provider,error in (('claude',claude),('grok',stream.result(0)['error'])):
            task=new_id()
            result=dict(worker=dict(provider=provider,title='fixture',native_session_id=new_id()),task_id=task,
                        model_requested='exact',model_observed=None,status='failed',summary='Earlier model text',error=error,result_artifact_id='art_fixture')
            context=dict(initial={'FAILED':1},latest_phase=3,latest={'RUNNING':1},active=1,queued=0)
            text=completion('group',1,[result],context=context)
            assert 'phase 1' in text and task in text
            assert 'BUDGET_EXHAUSTED' in text and 'limits.max_native_turns_per_task configured 1' in text
            assert ('observed '+str(count) if count is not None else 'observed not available') in text
            assert "initial {'FAILED': 1}" in text and 'active 1' in text
            assert 'do not replay accepted or uncertain input' in text
            assert 'npeer result '+task+' --compact' in text
