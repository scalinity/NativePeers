"""Secret redaction for text persisted in the activity log. Provider homes are never touched."""
from __future__ import annotations

import re

MARK = '[REDACTED]'
PEM = re.compile(r'-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?(?:-----END [A-Z0-9 ]*PRIVATE KEY-----|\Z)', re.S)
TOKEN = re.compile(r'\b(?:sk-[A-Za-z0-9_\-]{16,}|xai-[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
                   r'|AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_\-]{30,}|xox[abprs]-[A-Za-z0-9\-]{10,}|npm_[A-Za-z0-9]{30,}'
                   r'|eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,})')
BEARER = re.compile(r'(?i)\b(bearer|basic)(\s+)[A-Za-z0-9._~+/=\-]{12,}')
USERINFO = re.compile(r'(?<=://)[^\s/:@]+:[^\s/@]+(?=@)')
ASSIGN = re.compile(r'''(?ix)\b([A-Za-z0-9_.\-]*(?:api[_\-]?key|access[_\-]?key|secret|token|passw(?:or)?d|credentials?|private[_\-]?key|authorization)[A-Za-z0-9_.\-]*)
                        (\s*[=:]\s*)(["']?)(?!\d+(?![\w]))([^\s"',;]{6,})\3''')


def redact(text: str) -> str:
    if not text:
        return text
    text = PEM.sub(MARK, text)
    text = TOKEN.sub(MARK, text)
    text = BEARER.sub(lambda m: m.group(1) + m.group(2) + MARK, text)
    text = USERINFO.sub(MARK, text)
    return ASSIGN.sub(lambda m: m.group(1) + m.group(2) + m.group(3) + MARK + m.group(3), text)
