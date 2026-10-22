import io
import json
import os
from pathlib import Path
import shlex
import sys
from unittest.mock import patch

import pytest

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.usage.install import install,entry_key
from nativepeers.usage.statusline import capture,forward,signature
from nativepeers.usage import store


@pytest.fixture
def fixture(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home';home.mkdir();binary=tmp_path/'native';binary.write_text('native executable')
    yield j,home,binary
    j.close()


def test_capture_filters_private_fields_and_never_rebinds_peers(fixture):
    j,home,binary=fixture;contract={'runtime_stat':signature(binary),'runtime_sha256':'fixture'}
    value={'session_id':'12345678-abcd-4f01-a345-123456789abc','version':'2.1.308','model':{'id':'claude-opus-5-5'},
        'email':'PRIVATE_EMAIL','transcript_path':'PRIVATE_TRANSCRIPT','cwd':'PRIVATE_CWD',
        'rate_limits':{'five_hour':{'used_percentage':25},'seven_day_opus':{'used_percentage':100}}}
    with patch('nativepeers.usage.statusline.provider_home',return_value=home):
        with pytest.raises(PeerError):capture(j,'claude',home,value,contract,owners=[])
        capture(j,'claude',home,value,contract,owners=[{'executable':str(binary)}])
        assert j.one('SELECT COUNT(*) n FROM peer_instances')['n']==0
        raw=j.one('SELECT payload_json FROM usage_observations')['payload_json']
        assert 'PRIVATE_' not in raw and 'session_id' not in raw
        assert len(json.loads(raw)['pools'])==2
        assert json.loads(raw)['pools'][1]['models']==[]
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'PRIVATE_KEY'},clear=True):
            capture(j,'claude',home,value,contract,owners=[{'executable':str(binary)}])
            observation=store.latest(j,'claude',home,'snapshot')
            assert observation['payload']['route']=='api-key'
            assert 'PRIVATE_KEY' not in json.dumps(observation)
        binary.write_text('changed runtime')
        with pytest.raises(PeerError) as error:capture(j,'claude',home,value,contract,owners=[{'executable':str(binary)}])
        assert error.value.code=='CAPABILITY_MISMATCH'


def test_forward_preserves_input_output_exit_and_large_payload(tmp_path,capfd):
    raw=b'full original payload\x00'+b'x'*300000
    output=tmp_path/'original-input'
    code="import sys;from pathlib import Path;Path("+repr(str(output))+").write_bytes(sys.stdin.buffer.read());print('ORIGINAL_DISPLAY');sys.exit(7)"
    captured,status=forward(io.BytesIO(raw),shlex.join([sys.executable,'-c',code]))
    assert output.read_bytes()==raw and captured is None and status==7
    assert capfd.readouterr().out=='ORIGINAL_DISPLAY\n'


def test_install_uninstall_preserve_disabled_custom_and_later_settings(fixture):
    j,home,binary=fixture;path=home/'settings.json';previous={'type':'command','command':'printf original','padding':2}
    path.write_text(json.dumps({'statusLine':previous,'unrelated':False}))
    with patch('nativepeers.usage.install.executable',return_value=binary),patch('nativepeers.usage.install.verify',return_value={'sha256':'approved'}):
        before=j.db.total_changes
        plan=install(j,providers=['claude'],homes={'claude':home},dry_run=True)
        assert plan['changed'] and j.db.total_changes==before and json.loads(path.read_text())['statusLine']==previous
        assert install(j,providers=['claude'],homes={'claude':home})['changed']
        assert not install(j,providers=['claude'],homes={'claude':home})['changed']
        data=json.loads(path.read_text());data['later']=False;path.write_text(json.dumps(data))
        assert install(j,remove=True)['changed']
        assert json.loads(path.read_text())=={'statusLine':previous,'unrelated':False,'later':False}
        data=json.loads(path.read_text());data['statusLine']['enabled']=False;path.write_text(json.dumps(data));before=path.read_bytes()
        result=install(j,providers=['claude'],homes={'claude':home})
        assert not result['changed'] and result['preserved'] and path.read_bytes()==before


def test_interrupted_install_and_uninstall_recover_without_clobber(fixture):
    from nativepeers.install import compare_write
    j,home,binary=fixture;path=home/'settings.json';path.write_text('{"keep":false}')
    with patch('nativepeers.usage.install.executable',return_value=binary),patch('nativepeers.usage.install.verify',return_value={'sha256':'approved'}):
        def after_write(*args):compare_write(*args);raise OSError('after atomic write')
        with patch('nativepeers.usage.install.compare_write',side_effect=after_write):
            with pytest.raises(OSError):install(j,providers=['claude'],homes={'claude':home})
        install(j,providers=['claude'],homes={'claude':home})
        with patch('nativepeers.usage.install.compare_write',side_effect=after_write):
            with pytest.raises(OSError):install(j,remove=True)
        install(j,remove=True)
        assert json.loads(path.read_text())=={'keep':False}
        assert not json.loads((j.root/'usage-install.json').read_text())['providers']


def test_multiple_selected_homes_have_separate_contracts(fixture):
    j,home,binary=fixture;other=home/'other';other.mkdir()
    with patch('nativepeers.usage.install.executable',return_value=binary),patch('nativepeers.usage.install.verify',return_value={'sha256':'approved'}):
        for h in (home,other):install(j,providers=['claude'],homes={'claude':h})
        assert len(json.loads((j.root/'usage-install.json').read_text())['providers'])==2
        install(j,remove=True)
        assert all('statusLine' not in json.loads((h/'settings.json').read_text()) for h in (home,other))


def test_explicit_null_and_later_owned_edit_are_preserved(fixture):
    j,home,binary=fixture;path=home/'settings.json';path.write_text('{"statusLine":null,"keep":false}')
    with patch('nativepeers.usage.install.executable',return_value=binary),patch('nativepeers.usage.install.verify',return_value={'sha256':'approved'}):
        install(j,providers=['claude'],homes={'claude':home});install(j,remove=True)
        assert json.loads(path.read_text())=={'statusLine':None,'keep':False}
        install(j,providers=['claude'],homes={'claude':home})
        data=json.loads(path.read_text());data['statusLine']['padding']=5;path.write_text(json.dumps(data));before=path.read_bytes()
        assert install(j,remove=True)['preserved'][0]['state']=='MODIFIED'
        assert path.read_bytes()==before


def test_scoped_statusline_receipt_does_not_approve_workers(fixture):
    from nativepeers.compatibility import fingerprint,verify
    from nativepeers.files import atomic_json
    from nativepeers.usage.collectors import verify_usage
    j,home,binary=fixture;runtime=fingerprint(binary)
    atomic_json(j.root/'compat/runtime.json',{'providers':{'claude':dict(runtime,sha256='old-approved-runtime')}})
    with pytest.raises(PeerError):verify_usage(j,'claude',binary)
    atomic_json(j.root/'compat/usage-runtime.json',{'claude':{'contract':'native-statusline-v1',
        'proof':'verified-zero-input-native-statusline','runtime':runtime}})
    assert verify_usage(j,'claude',binary)==runtime
    with pytest.raises(PeerError):verify(j,'claude',binary)
    binary.write_text('changed after approval')
    with pytest.raises(PeerError):verify_usage(j,'claude',binary)
