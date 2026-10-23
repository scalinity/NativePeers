"""Dirty tracked bytes and explicitly selected bytes share one input budget."""
import pytest

from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.models import ARTIFACT_MAX
from nativepeers.worktrees import freeze,git,prepare,snapshot


def test_combined_input_budget_refuses_before_materialization_and_accepts_boundary(tmp_path):
    source=tmp_path/'source';source.mkdir();(source/'tracked.bin').write_bytes(b'base\0')
    git(source,'init','-q');git(source,'add','tracked.bin');git(source,'commit','-qm','Create input budget fixture')
    workspace={'mode':'isolated-worktree','untracked_allowlist':['selected.bin']}
    journal=Journal(tmp_path/'state')
    try:
        (source/'tracked.bin').write_bytes(b'\0'*(6*1024*1024));(source/'selected.bin').write_bytes(b'\0'*(6*1024*1024))
        before=(source/'.git/index').read_bytes()
        for operation in (lambda:snapshot(source,untracked_allowlist=['selected.bin']),
                          lambda:freeze(source,[workspace]),lambda:prepare(journal,source,'over',workspace)):
            with pytest.raises(PeerError) as error:operation()
            assert error.value.code=='WORKSPACE_SNAPSHOT_REQUIRED'
        assert not (source/'.worktrees').exists()
        assert (source/'.git/index').read_bytes()==before
        (source/'tracked.bin').write_bytes(b'\0'*(ARTIFACT_MAX//2));(source/'selected.bin').write_bytes(b'\0'*(ARTIFACT_MAX//2))
        frozen=freeze(source,[workspace])
        assert frozen.baseline['dirty_input_bytes']+sum(len(data) for data,mode in frozen.untracked.values())==ARTIFACT_MAX
        directory,_,_=prepare(journal,source,'boundary',workspace,frozen=frozen)
        from pathlib import Path
        assert (Path(directory)/'tracked.bin').stat().st_size+(Path(directory)/'selected.bin').stat().st_size==ARTIFACT_MAX
    finally:journal.close()
