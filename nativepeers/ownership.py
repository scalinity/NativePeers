"""Generation-specific process closure shared by resume and cleanup inspection."""
import json
from .process import owned_status


def release_status(journal, row, *, check_controller=True):
    path = journal.root/'workers'/(row['worker_id']+'.json')
    never = journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='controller-not-created' AND json_extract(payload_json,'$.generation')=? ORDER BY sequence DESC LIMIT 1", (row['worker_id'], row['generation']))
    if never:
        proof = json.loads(never['payload_json'])
        if proof.get('controller_created') is False and all(proof.get(k)==row[k] for k in ('worker_id','native_id','cwd','generation')):
            if not path.exists() or json.loads(path.read_text()).get('generation')!=row['generation']:
                return dict(released=True, generation=row['generation'], basis='controller-never-created', processes=[], reasons=[])
    result = dict(released=False, generation=row['generation'], basis='captured-process-and-pipe-closure', processes=[], reasons=[])
    if not path.exists():
        result['reasons'].append('Current-generation process manifest is missing')
        return result
    manifest = json.loads(path.read_text())
    if any(manifest.get(k) != row[k] for k in ('worker_id', 'generation', 'native_id')):
        result['reasons'].append('Process manifest does not match the current generation/history')
        return result
    records = ([manifest.get('owner')] if check_controller else []) + [manifest.get('native_process'), *manifest.get('native_descendants', [])]
    result['processes'] = [dict(pid=r['pid'], start=r['start'], state=owned_status(r)) for r in records if r]
    if any(r['state'] != 'DEAD' for r in result['processes']):
        result['reasons'].append('A captured process birth is live or cannot be inspected')
    cleanup = manifest.get('cleanup') or {}
    result['cleanup'] = cleanup
    if cleanup.get('state') != 'CLOSED' or cleanup.get('output_eof') is not True:
        result['reasons'].append('Positive native process and stdout/stderr EOF closure is missing')
    result['released'] = not result['reasons']
    return result
