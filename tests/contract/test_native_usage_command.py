import asyncio
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from nativepeers.errors import PeerError
from nativepeers.usage.native_command import run_native


def run(tmp_path,script,*,terminal=False,timeout=3,max_bytes=1048576):
    path=tmp_path/'native';path.write_text('#!/bin/sh\n'+script);path.chmod(0o700)
    with patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]):
        return asyncio.run(run_native('claude' if terminal else 'antigravity',path,[],tmp_path,terminal=terminal,timeout=timeout,max_bytes=max_bytes))


def test_native_no_stdin_and_drained_output(tmp_path):
    result=run(tmp_path,'read line && exit 9\nprintf \'{"ok":true}\'\nprintf warning >&2\n')
    assert result['exit_code']==0 and json.loads(result['stdout'])=={'ok':True}
    assert result['cleanup']['output_eof'] and result['submitted_commands']==0


def test_disposable_terminal_sends_only_one_local_usage_command(tmp_path):
    log=tmp_path/'input'
    script="printf 'Authentication failed during startup\\r\\n❯ '\nIFS= read -r command\nprintf '%s' \"$command\" > "+str(log)+"\nprintf '\\r\\nCurrent session: 20%% used\\r\\nCurrent week (all models): 80%% used\\r\\n'\n"
    result=run(tmp_path,script,terminal=True)
    assert log.read_text()=='/usage' and result['submitted_commands']==1
    assert b'Current session' in result['stdout']


def test_unknown_terminal_prompt_is_never_acknowledged(tmp_path):
    with pytest.raises(PeerError) as error:run(tmp_path,"printf 'Do you trust this folder?'\nsleep 10\n",terminal=True)
    assert error.value.code=='SOURCE_ACCESS_DENIED'


def test_timeout_is_bounded_and_owns_only_its_processes(tmp_path):
    with pytest.raises(PeerError) as error:run(tmp_path,'sleep 30\n',timeout=2.5)
    assert error.value.code in ('SOURCE_TIMEOUT','CLEANUP_UNCERTAIN')


def test_output_cap_is_enforced_before_a_payload_can_be_accepted(tmp_path):
    with pytest.raises(PeerError) as error:run(tmp_path,"printf '%01000d' 1\n",max_bytes=100)
    assert error.value.code=='SOURCE_OUTPUT_LIMIT'


def test_cancellation_reaps_the_exact_created_process(tmp_path):
    import sys
    from nativepeers.process import identity,matches
    path=tmp_path/'native';pid=tmp_path/'pid'
    path.write_text('#!'+sys.executable+'\nimport os,time\nfrom pathlib import Path\nPath('+repr(str(pid))+').write_text(str(os.getpid()))\ntime.sleep(30)\n');path.chmod(0o700)
    async def exercise():
        with patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]):
            task=asyncio.create_task(run_native('antigravity',path,[],tmp_path,timeout=3))
            async with asyncio.timeout(1):
                while not pid.exists():await asyncio.sleep(.01)
            owner=identity(int(pid.read_text())).json();task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert not matches(owner)
    asyncio.run(exercise())


def test_captured_descendant_pipes_are_bounded_after_leader_exit(tmp_path):
    import sys,time
    path=tmp_path/'native';child_file=tmp_path/'child'
    path.write_text('#!'+sys.executable+'\nimport os,subprocess,sys,time\nfrom pathlib import Path\np=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"])\nPath('+repr(str(child_file))+').write_text(str(p.pid))\ntime.sleep(.25)\n');path.chmod(0o700)
    started=time.monotonic()
    with patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]):
        with pytest.raises(PeerError) as error:asyncio.run(run_native('antigravity',path,[],tmp_path,timeout=2.7))
    assert error.value.code in ('SOURCE_TIMEOUT','CLEANUP_UNCERTAIN')
    from nativepeers.process import identity
    with pytest.raises(ProcessLookupError):identity(int(child_file.read_text()))
    assert time.monotonic()-started<3.5


@pytest.mark.parametrize('explicit',[False,True])
def test_claude_default_startup_layout_is_preserved_without_overriding_deliberate_selection(tmp_path,explicit):
    path=tmp_path/'native';path.write_text('#!/bin/sh\nprintf "%s" "${CLAUDE_CONFIG_DIR-unset}"\n');path.chmod(0o700)
    environment={'HOME':str(tmp_path),'PATH':os.environ['PATH']}
    if explicit:environment['CLAUDE_CONFIG_DIR']=str(tmp_path/'.claude')
    with patch.dict(os.environ,environment,clear=True),patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]):
        result=asyncio.run(run_native('claude',path,[],tmp_path/'.claude',timeout=3))
    assert result['stdout'].decode()==str(tmp_path/'.claude') if explicit else result['stdout']==b'unset'


@pytest.mark.parametrize('explicit',[False,True])
def test_owned_usage_layout_intent_wins_over_opposite_collector_environment(tmp_path,explicit):
    from nativepeers.usage.store import home_layout,scope
    path=tmp_path/'native';path.write_text('#!/bin/sh\nprintf "%s" "${CLAUDE_CONFIG_DIR-unset}"\n');path.chmod(0o700)
    environment={'HOME':str(tmp_path),'PATH':os.environ['PATH']}
    if not explicit:environment['CLAUDE_CONFIG_DIR']=str(tmp_path/'.claude')
    with patch.dict(os.environ,environment,clear=True),patch('nativepeers.usage.native_command.guarded_argv',side_effect=lambda b,a:[str(b),*a]):
        ambient=scope('claude',tmp_path/'.claude')
        with home_layout(explicit):
            intended=scope('claude',tmp_path/'.claude')
            result=asyncio.run(run_native('claude',path,[],tmp_path/'.claude',timeout=3))
        assert scope('claude',tmp_path/'.claude')==ambient and intended!=ambient
    assert result['stdout']==(str(tmp_path/'.claude').encode() if explicit else b'unset')
