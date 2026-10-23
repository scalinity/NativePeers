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


def test_generated_current_pointers_and_actual_sources_agree():
    cp=json.loads((ROOT/'evidence/checkpoint.json').read_text())
    evidence=json.loads((ROOT/cp['current_review']['evidence']).read_text())
    # Older historical records are intentionally immutable. New current records
    # use the generated publication contract.
    if 'reviewed_commit' not in evidence:return
    fp=json.loads((ROOT/'evidence/fingerprints.json').read_text())
    assert cp['stage']==evidence['stage'] and cp['updated_at']==evidence['updated_at']==fp['at']
    assert cp['git']['base_commit']==evidence['reviewed_commit']
    assert cp['publication']['verified_remote_code_commit']==evidence['code_commit']==fp['code_commit']
    assert cp['publication']['verified_remote_commit']==cp['git']['last_verified_publication']==evidence['code_commit']
    for name,digest in evidence['source'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest,name
    source_hash=hashlib.sha256(json.dumps(evidence['source'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    assert source_hash==evidence['source_map_sha256']==cp['current_review']['source_map_sha256']
    for runtime in evidence.get('runtime_scopes',[]):assert runtime.get('operation') and runtime.get('sha256')


def test_operation_index_separates_file_classes_and_native_check_scope():
    cp=json.loads((ROOT/'evidence/checkpoint.json').read_text())
    index=json.loads((ROOT/cp['current_evidence_index']).read_text())
    assert index['code_commit']==cp['current_review']['code_commit']
    assert set(index['file_classes'])=={'product','launchers','integrations','tests'}
    for category,group in index['file_classes'].items():
        assert group['file_count']==len(group['files']) and group['files'],category
        for path,digest in group['files'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==digest,path
        assert hashlib.sha256(json.dumps(group['files'],sort_keys=True,separators=(',',':')).encode()).hexdigest()==group['sha256']
    assert index['operation_checks']
    for check in index['operation_checks']:
        assert check['operation'] and check['parent_context'] and check['status'] in ('PASS','BLOCKED','NOT_RUN')
        if check.get('provider'):assert len(check['runtime_sha256'])==64
    assert 'documentation' in index['excluded_classes']
