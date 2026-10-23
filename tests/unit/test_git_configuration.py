"""Inspection and materialization use the same native Git normalization rules."""
from contextlib import closing
import os
from pathlib import Path

import pytest

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import ARTIFACT_MAX
from nativepeers.worktrees import changes,freeze,git,prepare,read_git


@pytest.mark.parametrize('configuration',['global','count'])
@pytest.mark.parametrize('large',[False,True])
def test_configured_clean_inputs_and_real_edits_agree_without_routing_elsewhere(tmp_path,monkeypatch,configuration,large):
    source=tmp_path/'source';source.mkdir();git(source,'init','-q')
    attributes=tmp_path/'attributes';attributes.write_text('input.txt text eol=crlf\n')
    # Commit with the existing fixture identity before selecting an isolated
    # global configuration. No author/committer configuration is changed.
    raw=b'clean input\r\n' if not large else b'x'*(ARTIFACT_MAX+3)+b'\r\n'
    (source/'input.txt').write_bytes(raw)
    git(source,'-c','core.attributesFile='+str(attributes),'add','input.txt')
    git(source,'commit','-qm','Create configured normalization fixture')
    other=tmp_path/'other';other.mkdir();git(other,'init','-q')
    (other/'original').write_text('unrelated\n');git(other,'add','.');git(other,'commit','-qm','Create unrelated routing fixture')
    def inventory():return {str(p.relative_to(other)):p.read_bytes() for p in (other/'.git').rglob('*') if p.is_file()}
    unrelated=inventory()
    if configuration=='global':
        config=tmp_path/'gitconfig';config.write_text('[core]\n\tattributesFile = '+str(attributes)+'\n')
        monkeypatch.setenv('GIT_CONFIG_GLOBAL',str(config))
    else:
        count=int(os.environ.get('GIT_CONFIG_COUNT','0'))
        monkeypatch.setenv('GIT_CONFIG_COUNT',str(count+1))
        monkeypatch.setenv('GIT_CONFIG_KEY_'+str(count),'core.attributesFile')
        monkeypatch.setenv('GIT_CONFIG_VALUE_'+str(count),str(attributes))
    assert git(source,'--no-optional-locks','diff','HEAD').stdout==b''
    # Ordinary configured Git can refresh stat metadata. Measure preservation
    # after that control command, before any NativePeers inspection.
    index=(source/'.git/index').read_bytes()
    for key,value in {'GIT_DIR':other/'.git','GIT_WORK_TREE':other,'GIT_INDEX_FILE':other/'.git/index',
                      'GIT_OBJECT_DIRECTORY':other/'.git/objects','GIT_ALTERNATE_OBJECT_DIRECTORIES':other/'.git/objects'}.items():
        monkeypatch.setenv(key,str(value))
    assert read_git(source,'diff','--no-ext-diff','--no-textconv','HEAD').stdout==b''
    assert (source/'.git/index').read_bytes()==index
    workspace={'mode':'isolated-worktree'}
    with closing(Journal(tmp_path/'state')) as journal:
        frozen=freeze(source,[workspace])
        assert frozen.baseline['dirty_input_files']==frozen.baseline['dirty_input_bytes']==0
        assert frozen.patch==b''
        assert (source/'.git/index').read_bytes()==index
        directory,_,baseline=prepare(journal,source,'clean',workspace,frozen=frozen)
        work=Path(directory)
        assert (source/'.git/index').read_bytes()==index
        assert (work/'input.txt').read_bytes()==raw
        noop=changes(work,baseline)
        assert noop['patch']==noop['worker_delta']==b''
        assert 'worker_delta_unavailable_reason' not in noop
        if not large:
            (work/'input.txt').write_bytes(b'worker edit\r\n')
            result=changes(work,baseline)
            assert b'+worker edit\n' in result['worker_delta']
            assert b'\r' not in result['worker_delta']
            assert result['patch']==result['worker_delta']
        (source/'input.txt').write_bytes(b'changed source\r\n')
        with pytest.raises(PeerError):prepare(journal,source,'refused',workspace,frozen=frozen)
        assert not (source/'.worktrees/refused').exists()
        assert (source/'input.txt').read_bytes()==b'changed source\r\n'
    assert (source/'.git/index').read_bytes()==index
    assert inventory()==unrelated
