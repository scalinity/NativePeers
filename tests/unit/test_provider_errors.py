import pytest

from nativepeers.provider_errors import classify,diagnostic
from nativepeers.claude_stream import Evidence


def test_claude_native_round_exhaustion_names_its_separate_cap():
    result=Evidence().failure({'subtype':'error_max_turns','is_error':True},1,native_max_turns=200)
    assert result['code']=='BUDGET_EXHAUSTED'
    assert 'max_native_turns_per_task' in result['message']
    assert result['limit']=={'field':'limits.max_native_turns_per_task','value':200,'native_flag':'--max-turns'}


@pytest.mark.parametrize('value,expected',[
    ({'message':'Authentication succeeded','error':{'code':'quota_exhausted'}},'QUOTA_EXHAUSTED'),
    ({'error':{'type':'authentication_error','message':'Unable to read quota'}},'AUTH_REQUIRED'),
    ({'error':{'type':'rate_limit_error','details':{'error_code':'enforced_spend_limit_reached'}}},'QUOTA_EXHAUSTED'),
    ({'error':{'type':'rate_limit_error'}},'RATE_LIMITED'),
    ({'error':'quota request timed out'},'PROVIDER_TASK_FAILED'),
    ({'context':'not logged in','error':'socket closed'},'PROVIDER_TASK_FAILED'),
    ({'account_id':'401-429','error':'network unavailable'},'PROVIDER_TASK_FAILED'),
    ({'code':401},'AUTH_REQUIRED'),({'status_code':429},'RATE_LIMITED'),
    ({'error':'Authentication succeeded; quota exhausted'},'QUOTA_EXHAUSTED'),
    ({'error':'Authentication failed and quota exhausted'},'PROVIDER_TASK_FAILED'),
    ({'error':{'code':'invalid_api_key','message':'Quota check failed'}},'AUTH_REQUIRED'),
    ({'terminal':{'error':{'code':'quota_exhausted'}},'message':'Please sign in'},'QUOTA_EXHAUSTED'),
])
def test_terminal_fields_take_precedence_over_wrapper_context(value,expected):
    assert classify(value)==expected


def test_terminal_stream_failure_supersedes_earlier_synthetic_auth():
    evidence=Evidence()
    evidence.observe({'type':'assistant','isApiErrorMessage':True,'error':'authentication_failed',
        'message':{'model':'<synthetic>','usage':{'input_tokens':0,'output_tokens':0},'content':[]}})
    result=evidence.failure({'subtype':'error','is_error':True,'error':{'code':'quota_exhausted'}},1)
    assert result['code']=='QUOTA_EXHAUSTED'
    assert result['may_have_received']
    # Labels cannot turn a possibly executed terminal result into replay proof.
    assert result['execution']['terminal_observed']


def test_provider_name_and_auth_instruction_are_not_borrowed():
    value=diagnostic({'error':{'code':'invalid_api_key'}},provider='antigravity')
    assert value['message'].startswith('Antigravity') and '/login' not in value['message']

@pytest.mark.parametrize('value,expected',[
    ({'terminal':{'error':{'type':'rate_limit_error'}},'error':{'type':'authentication_error'}},'RATE_LIMITED'),
    ({'code':'quota_exhausted','details':{'message':'Authentication failed in an earlier attempt'}},'QUOTA_EXHAUSTED'),
    ({'subtype':'error_max_turns','is_error':True},'BUDGET_EXHAUSTED'),
])
def test_authoritative_terminal_shapes(value,expected):
    assert classify(value)==expected
    if 'terminal' in value:
        e=Evidence();e.observe({'type':'assistant','isApiErrorMessage':True,'error':'authentication_error'})
        assert e.failure(value['terminal'],1)['code']==expected
