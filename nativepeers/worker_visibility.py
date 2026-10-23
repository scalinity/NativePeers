"""Worker-only native history classification receipts, separate from Desktop evidence."""
import json

from .errors import require
from .files import canonical

SOURCE={'custom':'nativepeers-worker'}
FLAG='--nativepeers-worker'


def requested(row,lease):
    if row['provider']!='codex':return None
    return lease.setdefault('history_visibility',dict(requested='nativepeers-worker',capability='UNKNOWN',
        runtime_acknowledged=False,persisted_source=None,desktop_verification='NOT_RUN',
        state='REQUESTED' if not row['native_id'] else 'LEGACY_UNVERIFIED'))


def save(journal,row,lease):
    journal.db.execute('UPDATE workers SET lease_json=? WHERE worker_id=? AND generation=?',
        (canonical(lease).decode(),row['worker_id'],row['generation']))


def server_arguments(row,lease):
    policy=requested(row,lease)
    # Already running legacy histories retain their exact pinned runtime. Never
    # add an unsupported option to an older lease or substitute its executable.
    return [FLAG] if policy is not None and (not row['native_id'] or policy['persisted_source']==SOURCE) else []


def initialized(journal,row,lease,response,*,required):
    policy=requested(row,lease)
    policy.update(runtime_acknowledged=response.get('nativepeersWorkerSource') is True,
                  capability='SUPPORTED' if response.get('nativepeersWorkerSource') is True else 'UNSUPPORTED')
    if required and not policy['runtime_acknowledged']:policy['state']='UNSUPPORTED'
    save(journal,row,lease)
    require(not required or policy['runtime_acknowledged'],'VISIBILITY_UNSUPPORTED',
        'The pinned Codex runtime did not acknowledge worker-only history classification; no thread or model input was requested',
        details={'history_visibility':policy})


def persisted(journal,row,lease,thread,*,required):
    policy=requested(row,lease)
    source=thread.get('source')
    policy.update(persisted_source=source,native_source_evidence='thread/start-or-resume response',
                  state='CLASSIFIED' if source==SOURCE else 'UNACKNOWLEDGED' if required else 'UNSUPPORTED')
    save(journal,row,lease)
    journal.event(row['worker_id'],'history-visibility',dict(policy,native_id=thread['id'],generation=row['generation']))
    require(not required or source==SOURCE,'VISIBILITY_UNACKNOWLEDGED',
        'Codex did not return the requested persisted worker source; native identity is retained and no model input was submitted',
        details={'native_id':thread['id'],'history_visibility':policy})


def status(row):
    return requested(row,json.loads(row.get('lease_json') or '{}'))
