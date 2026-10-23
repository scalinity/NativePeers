"""General usage keeps registered Claude layout rather than the lister's layout."""
import asyncio
import os
from pathlib import Path
import json
from unittest.mock import patch

import pytest

from nativepeers.caller import Caller
from nativepeers.errors import PeerError
from nativepeers.files import atomic_json
from nativepeers.journal import Journal
from nativepeers.launch import apply_home
from nativepeers.models import new_id
from nativepeers.process import identity
from nativepeers.registry import Registry
from nativepeers.usage import store
from nativepeers.usage.normalize import pool
from nativepeers.usage.service import Usage


@pytest.mark.parametrize('explicit',[False,True])
@pytest.mark.parametrize('selected_by',['label','caller'])
def test_general_usage_retains_layout_for_collection_storage_and_cached_read(tmp_path,explicit,selected_by,monkeypatch):
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path))
    j=Journal(tmp_path/'state');home=tmp_path/'.claude';home.mkdir();calls=[]
    try:
        with patch('pathlib.Path.home',return_value=tmp_path),patch.dict(os.environ,{'CLAUDE_CONFIG_DIR':str(home)}):
            peer=Registry(j).register('claude',home,new_id(),'fixture',identity(os.getpid()).json(),str(tmp_path),{},owner_kind='test',home_explicit=explicit)
        atomic_json(j.root/'policy.json',{'home_labels':{str(home.resolve()):'WORK'}})
        with store.home_layout(not explicit):
            store.record(j,'claude',home,{'source':'other-layout','pools':[pool('weekly',used=10,models=['*'])]})
        async def collect(journal,selected):
            env=apply_home('claude',selected,dict(os.environ),explicit=store._home_layout.get())
            calls.append(env.get('CLAUDE_CONFIG_DIR'))
            return {'source':'native','pools':[pool('weekly',used=100,models=['*'])],'models':[]}
        async def exercise():
            args={'provider':'claude','model':'sonnet'}
            caller=Caller('peer',peer['handle'],peer['incarnation_id']) if selected_by=='caller' else None
            if selected_by=='label':args['home_label']='WORK'
            with patch('pathlib.Path.home',return_value=tmp_path),patch.dict(os.environ,{},clear=False),patch('nativepeers.usage.service.COLLECTORS',{'claude':type('C',(),{'collect':staticmethod(collect)})()}):
                if explicit:os.environ.pop('CLAUDE_CONFIG_DIR',None)
                else:os.environ['CLAUDE_CONFIG_DIR']=str(home)
                fresh=await Usage(j).status(caller,refresh=True,**args)
                cached=await Usage(j).status(caller,**args)
                assert fresh['providers'][0]['scope']==cached['providers'][0]['scope']
                assert fresh['providers'][0]['state']==cached['providers'][0]['state']=='EXHAUSTED'
                assert fresh['providers'][0]['provider_home_explicit'] is explicit
        asyncio.run(exercise())
        assert calls==[str(home) if explicit else None]
    finally:j.close()


def test_label_refuses_conflicting_layouts(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'.claude';registry=Registry(j)
    try:
        for explicit in (False,True):registry.register('claude',home,new_id(),'fixture',identity(os.getpid()).json(),str(tmp_path),{},owner_kind='test',home_explicit=explicit)
        atomic_json(j.root/'policy.json',{'home_labels':{str(home.resolve()):'WORK'}})
        with pytest.raises(PeerError) as error:asyncio.run(Usage(j).status(provider='claude',home_label='WORK'))
        assert error.value.code=='AMBIGUOUS_TARGET'
    finally:j.close()
