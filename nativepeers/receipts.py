"""Evidence reduction: late uncertainty never erases stronger native evidence."""
RANK={'CREATED':0,'OUTBOX_COMMITTED':1,'SUBMITTING':2,'DELIVERY_UNKNOWN':2,
      'SUBMITTED_UNCONFIRMED':3,'HELD':4,'REJECTED':4,'ACCEPTED_QUEUED':5,
      'INGESTED':6,'MODEL_REPORTED_PROCESSED':7,'REPLY_RECORDED':8,'REPLY_SUBMITTED':9}


def reduce_receipt(current,event):
    if event not in RANK:raise ValueError('Unknown receipt stage')
    return event if RANK.get(current,-1)<=RANK[event] else current


def delivery_error(outcome):
    """Classify durable send/reply outcomes identically for every facade."""
    import json
    from .errors import PeerError
    state = outcome.get('state')
    if state != 'REJECTED' and state in RANK:
        return None
    info = json.loads((outcome.get('attempt') or {}).get('error_json') or '{}')
    return PeerError('PROVIDER_POLICY_REFUSED' if state == 'REJECTED' else info.get('code', state or 'PROTOCOL_ERROR'),
        info.get('message', 'Native recipient rejected delivery' if state == 'REJECTED' else 'Peer delivery failed before confirmed admission'),
        may_have_received=info.get('may_have_received', False), details=info.get('details'))
