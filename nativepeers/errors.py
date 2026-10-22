"""Stable, bounded errors shared by CLI, MCP, and local control."""
class PeerError(Exception):
    def __init__(self, code, message, *, may_have_received=False, details=None):
        super().__init__(message)
        self.code, self.message = code, message
        self.may_have_received, self.details = may_have_received, details

    def json(self):
        return dict(code=self.code, message=self.message,
                    may_have_received=self.may_have_received, details=self.details)


def require(condition, code, message, **kwargs):
    if not condition:
        raise PeerError(code, message, **kwargs)


class SupervisorNotStarted(PeerError):
    """Process creation itself failed; no supervisor could have admitted input."""
    def __init__(self, group_id, error):
        super().__init__('SUPERVISOR_LOST','Supervisor process creation failed before startup',
                         details={'group_id':group_id,'supervisor_started':False,'cause':type(error).__name__})
