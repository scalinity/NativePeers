"""The continuation record has one current scope; past evidence stays historical."""
import hashlib,json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


def test_checkpoint_current_verification_and_budget_match_their_sources():
    checkpoint=json.loads((ROOT/'evidence/checkpoint.json').read_text())
    evidence=json.loads((ROOT/checkpoint['current_review']['evidence']).read_text())
    assert checkpoint['tests_current']==evidence['tests']
    raw=(ROOT/checkpoint['live_budget']['path']).read_bytes();budget=json.loads(raw)
    assert checkpoint['live_budget']['sha256']==hashlib.sha256(raw).hexdigest()
    for key in ('ceiling','consumed_turns','reserved_turns','active_seconds'):
        assert checkpoint['live_budget'][key]==budget[key]
    assert checkpoint['outstanding_native_requests']==budget['outstanding_native_requests']
    assert 'native_acceptance_continuation' not in checkpoint
    assert 'pro_review' not in checkpoint
    assert all('status' in result for result in checkpoint['tests_current'].values())
    for name,document in checkpoint['canonical_documents'].items():
        assert hashlib.sha256((ROOT/'docs'/name).read_bytes()).hexdigest()==document['sha256']
    fingerprints=json.loads((ROOT/'evidence/fingerprints.json').read_text())
    assert fingerprints['canonical_and_dependency_sha256']['evidence/capabilities.json']==hashlib.sha256((ROOT/'evidence/capabilities.json').read_bytes()).hexdigest()
    assert fingerprints['current_verification']==checkpoint['current_review']['evidence']
    assert fingerprints['source_sha256']==evidence['source']
