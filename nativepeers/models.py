"""Stable identities, canonical frames, and explicit input limits."""
from dataclasses import asdict, dataclass
import hashlib
from pathlib import Path
import unicodedata
import uuid

from .errors import require
from .files import canonical

PEER_FRAME = 65536
INLINE_BODY = 12288
RESULT_PAGE = 262144
CONTROL_RESPONSE = 2097152
ARTIFACT_MAX = 10485760


def new_id():
    return str(uuid.uuid4())


def title(value):
    require(isinstance(value, str) and 0 < len(value) <= 512, "PROTOCOL_ERROR", "Invalid native title")
    require(not any(ord(c) < 32 for c in value), "PROTOCOL_ERROR", "Control characters in title")
    return unicodedata.normalize("NFC", value)


def bounded_text(value, limit=INLINE_BODY):
    require(isinstance(value, str), "PROTOCOL_ERROR", "Text must be a string")
    try:
        raw = value.encode("utf-8")
    except UnicodeError:
        require(False, "PROTOCOL_ERROR", "Malformed Unicode")
    require(len(raw) <= limit, "PAYLOAD_TOO_LARGE", f"Text exceeds {limit} UTF-8 bytes")
    require("\x00" not in value, "PROTOCOL_ERROR", "NUL in text")
    return value


@dataclass(frozen=True)
class PeerKey:
    host_id: str
    provider: str
    home_id: str
    native_session_id: str

    def __post_init__(self):
        require(self.provider in ("claude", "codex", "antigravity"), "UNSUPPORTED_HOST", "Unknown provider")
        try:
            uuid.UUID(self.native_session_id)
            uuid.UUID(self.host_id)
        except (ValueError, TypeError, AttributeError):
            require(False, "PROTOCOL_ERROR", "Native and host IDs must be UUIDs")
        require(len(self.home_id) == 64 and all(c in "0123456789abcdef" for c in self.home_id),
                "PROTOCOL_ERROR", "Invalid provider-home identity")

    @property
    def handle(self):
        return "np1_" + hashlib.sha256(canonical(asdict(self))).hexdigest()

    def json(self):
        return asdict(self)


def home_id(path: Path):
    return hashlib.sha256(str(path.expanduser().resolve()).encode()).hexdigest()


CLAUDE_ALIAS_FAMILIES={'sonnet':('sonnet',),'opus':('opus',),'haiku':('haiku',),'fable':('fable',),
                       'best':('fable','opus'),'opusplan':('sonnet',)}


def claude_model_matches(selection,observed,*,allow_multiple=False):
    """Known execution aliases; ambiguous account-selected aliases stay unknown."""
    selection=selection.removesuffix('[1m]');observed=observed.removesuffix('[1m]')
    if selection==observed:return True
    families=CLAUDE_ALIAS_FAMILIES.get(selection,())
    return bool(families and (allow_multiple or len(families)==1) and
                any(observed.startswith('claude-'+family+'-') for family in families))
