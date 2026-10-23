import json

import pytest

from nativepeers.errors import PeerError
from nativepeers.usage.antigravity_native import parse as agy
from nativepeers.usage.claude_native import Screen,parse as claude


def report(groups):
    return json.dumps({'status':'SUCCESS','conversation_id':'','num_turns':0,
        'usage':{'input_tokens':0,'output_tokens':0},'command':{'name':'usage','data':{'groups':groups}}})


def test_all_native_groups_windows_and_disabled_amounts_are_retained():
    pools=agy(report([{'name':'provider family','buckets':[
        {'id':'weekly','window':'weekly','remaining_fraction':.5,'reset_time':'2026-10-25T04:00:00Z'},
        {'id':'short','window':'5h','remaining':{'case':'remainingFraction','value':0}},
        {'id':'off','disabled':True,'remaining_fraction':0},
        {'id':'count','remaining':{'case':'remainingAmount','value':17}}]},
        {'name':'additional model allowance','buckets':[{'id':'model-weekly','remaining_fraction':.8}]}]))['pools']
    assert len(pools)==5 and pools[1]['remaining']==0 and not pools[2]['limiting']
    assert pools[3]['remaining']==17 and pools[3]['unit']=='native-amount'
    assert all(not p['models'] for p in pools)


@pytest.mark.parametrize('change',[{'num_turns':1},{'conversation_id':'a-session'},{'usage':{}},
    {'command':{'name':'credits','data':{}}},{'status':'FAILED'}, {'usage':{'input_tokens':0,'output_tokens':1}}])
def test_command_and_no_inference_proof_cannot_be_faked_by_success(change):
    value=json.loads(report([{'buckets':[{'id':'x','remaining_fraction':1}]}]));value.update(change)
    with pytest.raises(PeerError):agy(json.dumps(value))


def test_claude_scope_and_reset_ownership_do_not_collapse():
    text='Current session: 20% used · resets 2026-10-23T03:00:00Z\nCurrent week (all models): 70% used\nResets 11pm Eastern\nCurrent week (Sonnet only): 100% used\nCurrent week (Opus): 15% used'
    value=claude(text,fresh_transport=True)
    pools=value['pools'];assert [p['pool_id'] for p in pools]==['five_hour','seven_day','weekly/sonnet','weekly/opus']
    assert pools[0]['resets_at'] and pools[1]['resets_at'] is None and pools[2]['resets_at'] is None
    assert pools[2]['models']==['sonnet'] and pools[3]['models']==['opus']
    assert claude(text)['collection']['outcome']=='UNVERIFIED'
    cached=claude('Showing last-known usage as of 3 minutes ago\n'+text,fresh_transport=True)
    assert cached['collection']['outcome']=='CACHED' and cached['pools'][0]['observed_at']==0


def test_screen_clears_old_errors_across_split_ansi_updates():
    screen=Screen();screen.feed('Showing last-known usage\n');screen.feed('\x1b[');screen.feed('2J\x1b[HCurrent session: 20% used')
    assert 'last-known' not in screen.text()
    assert claude(screen.text(),fresh_transport=True)['collection']['outcome']=='FRESH'
    with pytest.raises(PeerError):claude("What's contributing to your limits usage?\nTop skills 80%")


def test_final_measured_claude_pane_supersedes_startup_auth_but_not_terminal_failure():
    assert claude('Authentication failed during startup\nCurrent session: 20% used',fresh_transport=True)['collection']['outcome']=='FRESH'
    with pytest.raises(PeerError) as error:claude('Current session: 20% used\nFailed to load usage',fresh_transport=True)
    assert error.value.code=='SOURCE_UNAVAILABLE'
    with pytest.raises(PeerError) as error:claude('Please run /login to read quota',fresh_transport=True)
    assert error.value.code=='SOURCE_ACCESS_DENIED'


def test_native_read_success_does_not_establish_backend_freshness_without_contract():
    raw=report([{'buckets':[{'id':'weekly','remaining_fraction':0}]}])
    unknown=agy(raw);fresh=agy(raw,fresh_verified=True)
    assert unknown['collection']['outcome']=='UNVERIFIED' and unknown['pools'][0]['observed_at']==0
    assert fresh['collection']['outcome']=='FRESH' and 'observed_at' not in fresh['pools'][0]


def test_credits_have_a_distinct_schema_unit_and_never_override_weekly_limits():
    from nativepeers.usage.antigravity_native import credits
    raw=json.loads(report([]));raw['command']={'name':'credits','data':{'remaining_credits':2980,'upgrade_uri':'PRIVATE_UNUSED'}}
    value=credits(json.dumps(raw))
    assert value['unit']=='credits' and value['remaining']==2980 and not value['limiting']
    assert 'PRIVATE_UNUSED' not in json.dumps(value)
    for invalid in (True,'2980',-1,None):
        raw['command']['data']['remaining_credits']=invalid
        with pytest.raises(PeerError):credits(json.dumps(raw))


def test_private_csi_keyboard_sequences_are_consumed_and_native_zero_metrics_are_optional():
    screen=Screen();screen.feed('\x1b[>4m\x1b[<u\x1b[4:3m❯ Try "fix errors"\x1b[0m')
    assert screen.text()=='❯ Try "fix errors"'
    base='Current session: 0% used\nCurrent week (all models): 100% used'
    assert claude(base,fresh_transport=True)['collection']['native_zero_invocation_tokens'] is None
    assert claude('Usage: 0 input, 0 output, 0 cache read, 0 cache write\n'+base,fresh_transport=True)['collection']['native_zero_invocation_tokens'] is True
    with pytest.raises(PeerError):claude('Usage: 1 input, 0 output, 0 cache read, 0 cache write\n'+base,fresh_transport=True)
