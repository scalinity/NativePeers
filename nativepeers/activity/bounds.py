"""Scrub, redact and bound event data before it reaches the queue, so queue memory is bounded too."""
from __future__ import annotations

import json
import math

from .redact import redact

MARKER = '\n… [truncated] …\n'
LEAF_FLOOR = 256


def clean(text):
    # Lone surrogates from JSON escapes cannot be written as UTF-8.
    return text if text.isascii() else text.encode('utf-8', 'replace').decode('utf-8')


def head_tail(text, limit):
    """(text, truncated, original_bytes): head and tail within limit UTF-8 bytes."""
    raw = text.encode('utf-8', 'replace')
    n = len(raw)
    if n <= limit:
        return text, False, n
    marker = MARKER.encode()
    budget = max(0, limit - len(marker))
    head = budget // 2
    tail = budget - head
    return (raw[:head].decode('utf-8', 'ignore') + MARKER + (raw[n - tail:].decode('utf-8', 'ignore') if tail else '')), True, n


def pre_cut(text, limit):
    """Bound the work redaction does on huge text; cuts snap to whitespace so a secret is never split."""
    keep = 2 * limit
    if len(text) <= 2 * keep:
        return text, False
    head = text[:keep]
    cut = max(head.rfind(' '), head.rfind('\n'))
    head = head[:cut] if cut > keep // 2 else head
    tail = text[-keep:]
    cut = min((i for i in (tail.find(' '), tail.find('\n')) if i >= 0), default=-1)
    tail = tail[cut + 1:] if 0 <= cut < keep // 2 else tail
    return head + MARKER + tail, True


def text_block(text, limit):
    """(redacted text within limit bytes, truncated, original bytes)."""
    text = clean(text if isinstance(text, str) else '' if text is None else str(text))
    original = len(text.encode('utf-8', 'replace'))
    short, cut = pre_cut(text, limit)
    value, trimmed, _ = head_tail(redact(short), limit)
    return value, cut or trimmed, original


def output_block(text, limit):
    """The inline `output` object of tool.completed; the full text is not retained, and the block says so."""
    value, truncated, original = text_block(text, limit)
    block = dict(text=value, truncated=truncated, bytes=original)
    if truncated:
        block.update(artifact_id=None, reason='full output is not retained')
    return block


def scrub(value, limit, depth=0):
    """Redact every string, drop non-finite numbers and cap breadth and depth."""
    if isinstance(value, str):
        return text_block(value, limit)[0]
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if depth >= 8:
        return text_block(str(value), LEAF_FLOOR)[0]
    if isinstance(value, dict):
        return {str(k)[:128]: scrub(v, limit, depth + 1) for k, v in list(value.items())[:256]}
    if isinstance(value, (list, tuple)):
        return [scrub(v, limit, depth + 1) for v in list(value)[:256]]
    return text_block(str(value), LEAF_FLOOR)[0]


def size(value):
    return len(json.dumps(value, separators=(',', ':'), ensure_ascii=False).encode('utf-8', 'replace'))


def leaves(value):
    stack = [value]
    while stack:
        node = stack.pop()
        items = node.items() if isinstance(node, dict) else enumerate(node) if isinstance(node, list) else ()
        for key, child in items:
            if isinstance(child, str):
                if len(child) > LEAF_FLOOR:
                    yield node, key, child
            else:
                stack.append(child)


def bound_data(data, limit):
    """Shrink the largest strings until the serialized data fits; head and tail survive, flags are set."""
    total = size(data)
    if total <= limit:
        return data
    for _ in range(64):
        found = list(leaves(data))
        if not found:
            break
        node, key, text = max(found, key=lambda leaf: len(leaf[2]))
        node[key] = head_tail(text, max(LEAF_FLOOR, len(text.encode('utf-8', 'replace')) // 2))[0]
        if isinstance(node, dict) and 'truncated' in node:
            node['truncated'] = True
        if size(data) <= limit:
            return data
    return dict(truncated=True, bytes=total, reason='event data exceeds the configured bound')
