"""Supported update checks use public compatibility and CLI boundaries."""
import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from nativepeers.compatibility import diagnose, fingerprint, update_verified, verify
from nativepeers.errors import PeerError
from nativepeers.journal import Journal


@pytest.fixture
def runtime(tmp_path):
    old=tmp_path/'old';old.write_text('qualified native')
    binary=tmp_path/'native'
    binary.write_text('#!/bin/sh\ncase "$1" in\n--version) echo "2.1.311 (Claude Code)";;\n--help) echo "--print --output-format stream-json --model --permission-mode dontAsk --tools --allowedTools --strict-mcp-config --mcp-config --settings --session-id --resume --name --restricted";;\nesac\n# peer_message_status orig_msg_id msgV PreToolUse SessionStart tool_use permission_mode\n')
    binary.chmod(0o700)
    j=Journal(tmp_path/'state')
    prior=dict(fingerprint(old),version='2.1.310 (Claude Code)',scope='fixture native worker/inbox')
    update_verified(j,{'claude':prior})
    yield j,binary,prior
    j.close()


def signed_run(original,callback=None):
    def run(argv,**kwargs):
        if argv[0]=='/usr/bin/codesign':
            return subprocess.CompletedProcess(argv,0,b'',b'Identifier=com.anthropic.claude-code\nTeamIdentifier=Q6L2SF6YDW\n')
        # The OS boundary is replaced on non-signed shell fixtures only.
        if argv[0]=='/usr/bin/sandbox-exec':argv=argv[3:]
        result=original(argv,**kwargs)
        if callback:callback(argv)
        return result
    return run


def test_untested_update_has_actionable_detail_without_changing_legacy_error(runtime):
    j,binary,_=runtime
    with pytest.raises(PeerError) as error:verify(j,'claude',binary)
    assert error.value.code=='CAPABILITY_MISMATCH'
    assert error.value.details['compatibility_state']=='UPDATE_UNTESTED'
    row=diagnose(j,{'claude':binary})[0]
    assert row['compatibility_state']=='UPDATE_UNTESTED'
    assert row['next_action']=='npeer runtime-check --provider claude --apply --json'


def test_supported_patch_renews_without_model_input_and_leaves_quota_unchanged(runtime):
    from nativepeers.cli import dispatch,parser
    j,binary,prior=runtime
    quota=j.root/'compat/usage-sources.json';quota.write_text('{}')
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        result=asyncio.run(dispatch(parser().parse_args(['runtime-check','--provider','claude','--apply']),j))
    assert result['approved'] and result['model_inputs']==0
    assert verify(j,'claude',binary)==fingerprint(binary)
    assert quota.read_text()=='{}'
    assert result['operations']==['worker_turns','inbox']
    assert result['native_acceptance']=='NOT_RUN'
    assert json.loads((j.root/'compat/runtime.json').read_text())['retained']['claude'][prior['sha256']]['workers']==[]


def test_observed_incompatibility_is_distinct_and_does_not_approve(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,_=runtime
    binary.write_text(binary.read_text().replace('--restricted','--removed-restriction'))
    before=json.loads((j.root/'compat/runtime.json').read_text())['providers']
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
    assert error.value.details['compatibility_state']=='INCOMPATIBLE'
    assert json.loads((j.root/'compat/runtime.json').read_text())['providers']==before
    assert diagnose(j,{'claude':binary})[0]['compatibility_state']=='INCOMPATIBLE'


def test_doctor_and_quota_keep_operation_specific_update_detail(runtime):
    from nativepeers.readiness import operation_readiness
    from nativepeers.usage.contracts import record,verified
    from nativepeers.usage.native_command import BROWSER_PROFILE
    import hashlib
    j,binary,_=runtime
    with patch('nativepeers.compatibility.diagnose',return_value=diagnose(j,{'claude':binary})),patch('nativepeers.readiness.readiness',return_value={'providers':{}}):
        row=operation_readiness(j)[0]
    assert row['operations']['worker_turns']['compatibility_state']=='UPDATE_UNTESTED'
    record(j,dict(provider='claude',qualified=True,runtime={'path':'old'},browser_guard_sha256=hashlib.sha256(BROWSER_PROFILE.encode()).hexdigest()))
    with pytest.raises(PeerError) as error:verified(j,'claude',binary)
    assert error.value.details['operation']=='quota-read'
    assert error.value.details['compatibility_state']=='UPDATE_UNTESTED'


@pytest.mark.parametrize('change',['executable','selected-link','approval','other-provider','same-bytes-mtime','after-write'])
def test_renewal_rechecks_runtime_and_prior_approval_at_the_write_boundary(runtime,tmp_path,change):
    from nativepeers.runtime_updates import check_runtime
    j,binary,prior=runtime
    selected=tmp_path/'selected';selected.symlink_to(binary)
    original=subprocess.run;changed=False
    other=tmp_path/'codex';other.write_text('separately qualified')
    receipt=j.root/'compat/runtime.json'
    def concurrent(argv):
        nonlocal changed
        if '--help' not in argv or changed:return
        changed=True
        if change=='executable':binary.write_text(binary.read_text()+'\n# changed while checking')
        if change=='selected-link':
            selected.unlink();selected.symlink_to(other)
        if change=='same-bytes-mtime':
            stat=binary.stat();data=binary.read_text();binary.write_text(data.replace('msgV','msgX'));os.utime(binary,ns=(stat.st_atime_ns,stat.st_mtime_ns))
        if change=='approval':update_verified(j,{'claude':dict(prior,scope='concurrently changed qualification')})
        if change=='other-provider':update_verified(j,{'codex':dict(fingerprint(other),version='fixture')})
    with patch('nativepeers.usage.collectors.executable',return_value=selected),patch('subprocess.run',side_effect=signed_run(original,concurrent)):
        if change=='after-write':
            from nativepeers.files import atomic_json
            def raced(path,document):
                atomic_json(path,document)
                binary.write_text(binary.read_text()+'\n# updated after write')
            with patch('nativepeers.compatibility.atomic_json',side_effect=raced),pytest.raises(PeerError) as error:
                check_runtime(j,'claude',apply=True)
            assert error.value.details['approval_written']
        elif change=='other-provider':
            assert check_runtime(j,'claude',apply=True)['approved']
            assert verify(j,'codex',other)==fingerprint(other)
        else:
            with pytest.raises(PeerError):check_runtime(j,'claude',apply=True)
            assert json.loads(receipt.read_text())['providers']['claude']['sha256']==prior['sha256']


@pytest.mark.parametrize('failure,state',[('signature','PROVENANCE_UNVERIFIED'),('series','QUALIFICATION_REQUIRED'),('protocol','INCOMPATIBLE'),('timeout','CHECK_UNAVAILABLE'),('missing','EXECUTABLE_NOT_FOUND')])
def test_unsupported_or_unavailable_check_never_approves(runtime,failure,state):
    from nativepeers.runtime_updates import check_runtime
    j,binary,prior=runtime
    original=subprocess.run
    if failure=='series':binary.write_text(binary.read_text().replace('2.1.311','3.0.0'))
    if failure=='protocol':binary.write_text(binary.read_text().replace('msgV','msgX'))
    if failure=='missing':binary.unlink()
    def boundary(argv,**kwargs):
        if failure=='signature':return subprocess.CompletedProcess(argv,1,b'',b'invalid')
        if failure=='timeout':raise subprocess.TimeoutExpired(argv,5)
        return signed_run(original)(argv,**kwargs)
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=boundary):
        with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
    assert (error.value.details or {}).get('compatibility_state',error.value.code)==state
    assert json.loads((j.root/'compat/runtime.json').read_text())['providers']['claude']==prior


def test_inspect_is_read_only_and_revoked_or_retired_runtime_cannot_be_renewed(runtime):
    from nativepeers.runtime_updates import check_runtime
    from nativepeers.compatibility import revoke
    j,binary,prior=runtime;receipt=j.root/'compat/runtime.json';before=receipt.read_bytes()
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        result=check_runtime(j,'claude')
        assert result['compatibility_state']=='SUPPORTED_UPDATE' and not result['approved']
        assert receipt.read_bytes()==before
        with pytest.raises(PeerError):verify(j,'claude',binary,renew=False)
        check_runtime(j,'claude',apply=True)
        update_verified(j,{'claude':prior})
        for revoked in (False,True):
            if revoked:revoke(j,'claude',fingerprint(binary)['sha256'])
            with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
            assert error.value.details['compatibility_state']==('REVOKED' if revoked else 'QUALIFICATION_REQUIRED')


@pytest.mark.parametrize('provider,prior_version,current_version',[('antigravity','1.4.5','1.4.7'),('grok','1.0.70','1.0.71')])
def test_other_supported_providers_use_their_own_contract(runtime,provider,prior_version,current_version):
    from nativepeers.runtime_updates import check_runtime,CONTRACTS
    j,binary,prior=runtime;contract=CONTRACTS[provider]
    binary.write_text('#!/bin/sh\ncase "$1" in\n--version) echo "'+current_version+'";;\n--help) echo "'+' '.join(contract['flags'])+'";;\nesac\n# '+' '.join(contract['markers'])+'\n')
    update_verified(j,{provider:dict(prior,version=prior_version)})
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        assert check_runtime(j,provider,apply=True)['approved']
    assert verify(j,provider,binary)==fingerprint(binary)
    assert json.loads((j.root/'compat/runtime.json').read_text())['providers']['claude']==prior


def test_approved_and_missing_runtime_diagnostics_choose_the_smallest_action(runtime,tmp_path):
    j,binary,prior=runtime
    approved=Path(prior['path'])
    row=diagnose(j,{'claude':approved})[0]
    assert row['compatibility_state']=='APPROVED' and 'approved' in row['reason']
    row=diagnose(j,{'claude':tmp_path/'missing'})[0]
    assert row['compatibility_state']=='EXECUTABLE_NOT_FOUND'
    assert 'Restore' in row['next_action']


def test_worker_renewal_does_not_grant_the_separate_statusline_operation(runtime):
    from nativepeers.runtime_updates import check_runtime
    from nativepeers.usage.collectors import verify_usage
    j,binary,_=runtime;original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        assert check_runtime(j,'claude',apply=True)['approved']
    with pytest.raises(PeerError) as error:verify_usage(j,'claude',binary)
    assert error.value.code=='CAPABILITY_MISMATCH'


def test_unreadable_executable_is_not_reported_as_missing_or_incompatible(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,_=runtime;original=Path.open
    def denied(path,*args,**kwargs):
        if path.resolve()==binary.resolve():raise PermissionError('fixture executable is not readable')
        return original(path,*args,**kwargs)
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch.object(Path,'open',new=denied):
        with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
        assert error.value.code=='CAPABILITY_MISMATCH'
        assert error.value.details['compatibility_state']=='CHECK_UNAVAILABLE'
        row=diagnose(j,{'claude':binary})[0]
        assert row['compatibility_state']=='CHECK_UNAVAILABLE'
        assert row['error']=='PermissionError'


def test_completed_series_refusal_remains_visible_in_passive_doctor(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,_=runtime;binary.write_text(binary.read_text().replace('2.1.311','3.0.0'))
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        with pytest.raises(PeerError):check_runtime(j,'claude',apply=True)
    row=diagnose(j,{'claude':binary})[0]
    assert row['compatibility_state']=='QUALIFICATION_REQUIRED'
    assert 'outside' in row['reason']


def test_same_version_rebuild_needs_qualification_instead_of_forward_patch_approval(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,prior=runtime;binary.write_text(binary.read_text().replace('2.1.311','2.1.310'))
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
    assert error.value.details['compatibility_state']=='QUALIFICATION_REQUIRED'
    assert json.loads((j.root/'compat/runtime.json').read_text())['providers']['claude']==prior


def test_ambiguous_version_banner_is_not_a_supported_patch(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,_=runtime;binary.write_text(binary.read_text().replace('2.1.311 (Claude Code)','loader 2.1.311 ; actual release 3.0.0'))
    original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        with pytest.raises(PeerError) as error:check_runtime(j,'claude',apply=True)
    assert error.value.details['compatibility_state']=='QUALIFICATION_REQUIRED'


def test_renewed_doctor_rows_keep_native_behavior_unrun(runtime):
    from nativepeers.runtime_updates import check_runtime
    from nativepeers.readiness import operation_readiness
    j,binary,_=runtime;original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        result=check_runtime(j,'claude',apply=True)
    row=diagnose(j,{'claude':binary})[0]
    assert row['native_acceptance']=='NOT_RUN' and 'NOT_RUN' in result['reason']
    with patch('nativepeers.compatibility.diagnose',return_value=[row]),patch('nativepeers.readiness.readiness',return_value={'providers':{}}):
        assert operation_readiness(j)[0]['operations']['worker_turns']['native_acceptance']=='NOT_RUN'


def test_swap_and_restore_cannot_approve_bytes_whose_options_failed(runtime):
    from nativepeers.runtime_updates import check_runtime
    j,binary,prior=runtime;good=binary.read_text();bad=good.replace('--restricted','--removed-restriction')
    binary.write_text(bad);original=subprocess.run
    def boundary(argv,**kwargs):
        if argv[0]=='/usr/bin/codesign':
            binary.write_text(good)
            return subprocess.CompletedProcess(argv,0,b'',b'')
        result=signed_run(original)(argv,**kwargs)
        if '--help' in argv:binary.write_text(bad)
        return result
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=boundary):
        with pytest.raises(PeerError):check_runtime(j,'claude',apply=True)
    assert json.loads((j.root/'compat/runtime.json').read_text())['providers']['claude']==prior


def test_bounded_admission_stays_distinct_from_native_qualification(runtime):
    from nativepeers.runtime_updates import check_runtime
    from nativepeers.readiness import operation_readiness
    j,binary,_=runtime;original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        result=check_runtime(j,'claude',apply=True)
        again=check_runtime(j,'claude')
    assert result['approved'] and result['compatibility_state']=='SUPPORTED_UPDATE'
    assert again['approved'] and again['compatibility_state']=='SUPPORTED_UPDATE'
    row=diagnose(j,{'claude':binary})[0]
    assert row['state']==row['compatibility_state']=='SUPPORTED_UPDATE'
    with patch('nativepeers.compatibility.diagnose',return_value=[row]),patch('nativepeers.readiness.readiness',return_value={'providers':{}}):
        assert operation_readiness(j)[0]['operations']['worker_turns']['state']=='SUPPORTED_UPDATE'
    assert verify(j,'claude',binary)==fingerprint(binary)


def test_quota_readiness_does_not_inherit_worker_operation_details(runtime):
    from nativepeers.readiness import operation_readiness
    j,binary,prior=runtime;update_verified(j,{'codex':prior})
    row=diagnose(j,{'codex':binary})[0]
    with patch('nativepeers.compatibility.diagnose',return_value=[row]),patch('nativepeers.readiness.readiness',return_value={'providers':{}}),patch('nativepeers.usage.collectors.executable',return_value=binary):
        quota=operation_readiness(j)[0]['operations']['quota']
    assert quota['operation']=='quota-read' and 'operations' not in quota
    assert quota['compatibility_state']=='UPDATE_UNTESTED'


def counted(original,calls):
    base=signed_run(original)
    def run(argv,**kwargs):
        calls.append(argv[0]);return base(argv,**kwargs)
    return run


def test_first_use_after_a_supported_patch_renews_once(runtime):
    j,binary,prior=runtime;calls=[];original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=counted(original,calls)):
        assert verify(j,'claude',binary)==fingerprint(binary)
        approval=json.loads((j.root/'compat/runtime.json').read_text())['providers']['claude']
        assert approval['sha256']==fingerprint(binary)['sha256'] and approval['update_basis']['previous']==prior['sha256']
        assert approval['native_acceptance']=='NOT_RUN' and approval['operations']==['worker_turns','inbox']
        seen=len(calls);assert seen
        assert verify(j,'claude',binary)==fingerprint(binary) and len(calls)==seen


def test_a_refused_update_is_recorded_and_not_retried_on_later_use(runtime):
    j,binary,_=runtime
    binary.write_text(binary.read_text().replace('--restricted','--removed-restriction'))
    receipt=j.root/'compat/runtime.json';before=json.loads(receipt.read_text())['providers'];calls=[];original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=counted(original,calls)):
        with pytest.raises(PeerError) as error:verify(j,'claude',binary)
        assert error.value.details['compatibility_state']=='INCOMPATIBLE'
        seen=len(calls);assert seen
        with pytest.raises(PeerError) as again:verify(j,'claude',binary)
        assert again.value.details['compatibility_state']=='INCOMPATIBLE' and len(calls)==seen
    assert json.loads(receipt.read_text())['providers']==before


def test_only_the_selected_runtime_renews_and_strict_verification_never_does(runtime,tmp_path):
    j,binary,_=runtime;calls=[];original=subprocess.run
    elsewhere=tmp_path/'elsewhere';elsewhere.write_text('another build')
    with patch('nativepeers.usage.collectors.executable',return_value=elsewhere),patch('subprocess.run',side_effect=counted(original,calls)):
        with pytest.raises(PeerError) as error:verify(j,'claude',binary)
    assert error.value.details['compatibility_state']=='UPDATE_UNTESTED' and not calls
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=counted(original,calls)):
        with pytest.raises(PeerError) as strict:verify(j,'claude',binary,renew=False)
    assert strict.value.details['compatibility_state']=='UPDATE_UNTESTED' and not calls


def test_a_transient_failure_backs_off_then_retries_and_codex_is_left_alone(runtime):
    from nativepeers.runtime_updates import renew_selected
    j,binary,_=runtime;calls=[]
    def timing_out(argv,**kwargs):
        calls.append(argv[0]);raise subprocess.TimeoutExpired(argv,5)
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=timing_out):
        for _ in range(2):
            with pytest.raises(PeerError) as error:verify(j,'claude',binary)
            assert error.value.details['compatibility_state']=='UPDATE_UNTESTED'
        assert len(calls)==1
        with patch('nativepeers.runtime_updates.time.time',return_value=time.time()+120):
            with pytest.raises(PeerError):verify(j,'claude',binary)
        assert len(calls)==2
        assert renew_selected(j,'codex',fingerprint(binary)) is False and len(calls)==2


def test_revoked_or_retired_runtime_is_never_renewed_on_use(runtime):
    from nativepeers.runtime_updates import check_runtime
    from nativepeers.compatibility import revoke
    j,binary,prior=runtime;original=subprocess.run
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=signed_run(original)):
        check_runtime(j,'claude',apply=True);update_verified(j,{'claude':prior})
    for revoked in (False,True):
        if revoked:revoke(j,'claude',fingerprint(binary)['sha256'])
        calls=[]
        with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=counted(original,calls)):
            with pytest.raises(PeerError) as error:verify(j,'claude',binary)
        assert error.value.details['compatibility_state']==('REVOKED' if revoked else 'QUALIFICATION_REQUIRED') and not calls


def test_a_worker_without_a_pinned_runtime_renews_but_a_pinned_one_stays_strict(runtime):
    j,binary,prior=runtime;original=subprocess.run;now=fingerprint(binary)
    def worker(worker_id,lease):
        j.db.execute('INSERT INTO workers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                     (worker_id,None,None,'claude',None,'role',None,None,None,'/tmp',None,'RESERVED',1,json.dumps(lease)))
        j.db.commit()
    worker('pinned',dict(verified_runtime=now));worker('fresh',{})
    calls=[]
    with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('subprocess.run',side_effect=counted(original,calls)):
        with pytest.raises(PeerError) as error:verify(j,'claude',binary,worker_id='pinned')
        assert error.value.details['compatibility_state']=='UPDATE_UNTESTED' and not calls
        assert verify(j,'claude',binary,worker_id='fresh')==now and calls

