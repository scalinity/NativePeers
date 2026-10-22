"""Shared completion claims for supervised and retained-result waits."""
import time
import json
from pathlib import Path


def wait_generations(group_id,policy,handle):
    phases=policy.get('phases',{'1':policy['initial_tasks']})
    if handle==group_id:return sorted(int(n) for n in phases)
    # Initial task reads leave the batch route available, even for a one-task batch.
    return [int(n) for n,tasks in phases.items() if int(n)!=1 and tasks==[handle]]


def claim_wait(journal,group_id,generation,controller_generation,waiter,timeout,*,db=None):
    if generation is None:return False
    if db is None:
        with journal.transaction() as db:
            return claim_wait(journal,group_id,generation,controller_generation,waiter,timeout,db=db)
    now=time.time()
    return bool(db.execute("UPDATE completion_routes SET state='WAIT_LEASED',waiter_id=?,lease_deadline=?,claimed_generation=? WHERE group_id=? AND completion_generation=? AND (state='UNCLAIMED' OR (state='WAIT_LEASED' AND (lease_deadline<? OR (waiter_id=? AND claimed_generation=?))))",
        (waiter,now+timeout+1,controller_generation,group_id,generation,now,waiter,controller_generation)).rowcount)


def release_wait(journal,group_id,controller_generation,waiter,mode):
    journal.db.execute("UPDATE completion_routes SET state='UNCLAIMED',mode=?,waiter_id=NULL,lease_deadline=NULL WHERE group_id=? AND state='WAIT_LEASED' AND waiter_id=? AND claimed_generation=?",
        ('push' if mode=='push' else 'wait',group_id,waiter,controller_generation))


def wait_output(journal,group_id,generation,controller_generation,waiter,*,settle=True):
    # Tool output can be lost after this point. Keep the claim rather than push a duplicate.
    changed=bool(journal.db.execute("UPDATE completion_routes SET state='NOTIFICATION_UNKNOWN',lease_deadline=NULL WHERE group_id=? AND completion_generation=? AND state='WAIT_LEASED' AND waiter_id=? AND claimed_generation=?",
        (group_id,generation,waiter,controller_generation)).rowcount)
    if changed and settle:
        row=journal.one('SELECT delivery_id FROM completion_routes WHERE group_id=? AND completion_generation=?',(group_id,generation))
        settle_deferred(journal,group_id,row['delivery_id'])
    return changed


def settle_deferred(journal,group_id,delivery_id):
    """Only an irreversibly retained/tool-owned, definitely unsubmitted notice."""
    test=journal.root/'test-budget.json'
    if not test.exists():return
    attempt=journal.one("SELECT a.state FROM attempts a JOIN send_requests r USING(message_id) JOIN messages m USING(message_id) WHERE r.client_request_id=? AND json_extract(m.payload_json,'$.from.kind')='controller' AND json_extract(m.payload_json,'$.from.group_id')=?",(delivery_id,group_id))
    if not attempt or attempt['state'] not in ('OUTBOX_COMMITTED','ADMISSION_DEFERRED'):return
    event=journal.one("SELECT payload_json FROM events WHERE subject_id=? AND kind='completion-budget-reserved' ORDER BY sequence DESC LIMIT 1",(delivery_id,))
    if not event:return
    from .budget import LiveBudget
    spec=json.loads(test.read_text());reservation=json.loads(event['payload_json'])['reservation']
    if LiveBudget(Path(spec['path'])).release_unsubmitted(reservation):
        journal.event(delivery_id,'completion-budget-released',{'reservation':reservation,'reason':'definitely-unsubmitted-result-retained'})
