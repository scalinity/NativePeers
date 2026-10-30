"""`npeer activity WORKER_ID`: the CLI face of the reader, with the authority of `npeer result` for that worker."""
from __future__ import annotations

from ..errors import require
from .reader import Reader, index


async def activity(core, caller, args):
    journal = core.journal
    worker = journal.one('SELECT worker_id,group_id FROM workers WHERE worker_id=?', (args.worker_id,))
    require(worker, 'PROTOCOL_ERROR', 'Unknown worker')
    group = core.delegation.group(caller, worker['group_id'])
    if caller.key != group['parent_key']:
        require(caller.grants.get('worker_id') == worker['worker_id'], 'APPROVAL_BLOCKED', 'A worker can read only its own activity')
    require(args.after >= 0 and args.max >= 1 and args.wait_ms >= 0, 'PROTOCOL_ERROR', 'after and wait-ms cannot be negative and max must be positive')
    page = await Reader(journal.root, worker['worker_id']).wait(args.after, wait_ms=args.wait_ms, limit=args.max, item=args.item)
    return dict(worker_id=worker['worker_id'], group_id=worker['group_id'], after=args.after, **page, tasks=index(journal, worker['worker_id']))
