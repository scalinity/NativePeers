"""Shared completion claims for supervised and retained-result waits."""
import time


def wait_generation(group_id,policy,handle):
    phases=policy.get('phases',{'1':policy['initial_tasks']})
    if handle==group_id:return max(int(n) for n in phases)
    # Initial task reads leave the batch route available, even for a one-task batch.
    return next((int(n) for n,tasks in phases.items() if int(n)!=1 and tasks==[handle]),None)


def claim_wait(journal,group_id,generation,controller_generation,waiter,timeout):
    if generation is None:return False
    now=time.time()
    with journal.transaction() as db:
        return bool(db.execute("UPDATE completion_routes SET state='WAIT_LEASED',waiter_id=?,lease_deadline=?,claimed_generation=? WHERE group_id=? AND completion_generation=? AND (state='UNCLAIMED' OR (state='WAIT_LEASED' AND lease_deadline<?))",
            (waiter,now+timeout+1,controller_generation,group_id,generation,now)).rowcount)


def wait_output(journal,group_id,generation,controller_generation,waiter):
    # Tool output can be lost after this point. Keep the claim rather than push a duplicate.
    return bool(journal.db.execute("UPDATE completion_routes SET state='NOTIFICATION_UNKNOWN',lease_deadline=NULL WHERE group_id=? AND completion_generation=? AND state='WAIT_LEASED' AND waiter_id=? AND claimed_generation=?",
        (group_id,generation,waiter,controller_generation)).rowcount)
