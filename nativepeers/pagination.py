"""Small scoped cursors for stable bounded journal reads."""
import base64
import json

from .errors import PeerError,require
from .files import canonical

SQLITE_MAX=2**63-1


def encode(scope,position,ceiling):
    return base64.urlsafe_b64encode(canonical({'scope':scope,'position':position,'ceiling':ceiling})).decode().rstrip('=')


def decode(cursor,scope):
    require(isinstance(cursor,str) and 0<len(cursor)<=4096,'PROTOCOL_ERROR','Invalid page cursor')
    try:
        value=json.loads(base64.b64decode(cursor+'='*(-len(cursor)%4),altchars=b'-_',validate=True))
        require(isinstance(value,dict) and value.get('scope')==scope and type(value.get('ceiling')) is int and 0<=value['ceiling']<=SQLITE_MAX,
                'PROTOCOL_ERROR','Cursor belongs to a different caller or query')
        return value['position'],value['ceiling']
    except (ValueError,KeyError,TypeError) as error:
        raise PeerError('PROTOCOL_ERROR','Invalid page cursor') from error
