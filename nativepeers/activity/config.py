"""Activity-log bounds, read from $NPEER_HOME/config.toml [activity]. A bad value never stops a worker."""
from __future__ import annotations

from pathlib import Path
import tomllib

DEFAULTS = dict(event_data_bytes=65536, tool_output_bytes=16384, log_bytes=67108864)
MINIMUMS = dict(event_data_bytes=4096, tool_output_bytes=256, log_bytes=1048576)
LONG_POLL_CAP_MS = 25000
PAGE_EVENTS = 512
PAGE_BYTES = 1000000


def limits(root):
    values = dict(DEFAULTS)
    path = Path(root) / 'config.toml'
    try:
        section = tomllib.loads(path.read_text()).get('activity', {}) if path.is_file() else {}
    except (OSError, ValueError):
        section = {}
    if isinstance(section, dict):
        for key in DEFAULTS:
            value = section.get(key)
            if type(value) is int and value >= MINIMUMS[key]:
                values[key] = value
    return values
