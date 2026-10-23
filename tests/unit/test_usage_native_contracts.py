"""Usage transport approvals never expand worker/runtime approval or native auth."""
import asyncio
import hashlib
from pathlib import Path
from unittest.mock import patch,AsyncMock

import pytest

from nativepeers.compatibility import fingerprint
from nativepeers.errors import PeerError
from nativepeers.journal import Journal
from nativepeers.usage.contracts import record,verified
from nativepeers.usage.collectors import NativeCollector
from nativepeers.usage.native_command import BROWSER_PROFILE
from nativepeers.usage.service import Usage


def receipt(binary,provider):
    return dict(provider=provider,qualified=True,runtime=fingerprint(binary),
        browser_guard_sha256=hashlib.sha256(BROWSER_PROFILE.encode()).hexdigest(),route='native-print-usage')


def test_usage_approval_is_scoped_to_the_exact_runtime_and_claude_authorization(tmp_path):
    j=Journal(tmp_path/'state');binary=tmp_path/'native';binary.write_text('original')
    try:
        record(j,receipt(binary,'antigravity'));assert verified(j,'antigravity',binary)['qualified']
        assert not (j.root/'compat/runtime.json').exists()
        binary.write_text('different')
        with pytest.raises(PeerError,match='changed'):verified(j,'antigravity',binary)
        record(j,receipt(binary,'claude'))
        with pytest.raises(PeerError,match='not authorized'):verified(j,'claude',binary)
    finally:j.close()


def test_antigravity_alternate_home_is_refused_before_native_process(tmp_path):
    j=Journal(tmp_path/'state');binary=tmp_path/'native';binary.write_text('fixture')
    try:
        record(j,receipt(binary,'antigravity'))
        with patch('nativepeers.usage.collectors.executable',return_value=binary),patch('nativepeers.usage.native_command.run_native',new_callable=AsyncMock) as launch:
            with pytest.raises(PeerError) as error:asyncio.run(NativeCollector('antigravity').collect(j,tmp_path/'unqualified-home'))
        assert error.value.code=='SOURCE_HOME_MISMATCH';launch.assert_not_awaited()
    finally:j.close()


def test_missing_usage_executable_and_source_denial_never_claim_native_logout(tmp_path):
    j=Journal(tmp_path/'state');home=tmp_path/'home'
    try:
        for failure,expected in [(FileNotFoundError(),'EXECUTABLE_NOT_FOUND'),(PeerError('AUTH_REQUIRED','source read denied'),'SOURCE_ACCESS_DENIED')]:
            collect=AsyncMock(side_effect=failure)
            with patch('nativepeers.usage.service.COLLECTORS',{'claude':type('C',(),{'collect':collect})()}):asyncio.run(Usage(j).refresh('claude',home,force=True))
            view=Usage(j).snapshot('claude',home)
            assert view['collector_state']==expected and view['state']=='UNKNOWN'
    finally:j.close()
