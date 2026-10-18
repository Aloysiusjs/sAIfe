"""Secret redaction for logs and traces (PRD N4).

Two layers: exact-match removal of the configured key values, plus patterns
for anything that looks like an API key or bearer token.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

REDACTED = "[REDACTED]"

_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{16,}"),
    re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}"),  # Google API keys
]

_known_secrets: set[str] = set()


def register_secrets(values: Iterable[str | None]) -> None:
    """Remember exact secret values so they are redacted even if they match no pattern."""
    for value in values:
        if value and len(value) >= 8:
            _known_secrets.add(value)


def redact(text: str) -> str:
    for secret in _known_secrets:
        text = text.replace(secret, REDACTED)
    for pattern in _PATTERNS:
        text = pattern.sub(REDACTED, text)
    return text


class RedactingFilter(logging.Filter):
    """Rewrites each record's fully formatted message with secrets removed."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(record.getMessage())
        record.args = None
        return True


def install_redaction(logger: logging.Logger | None = None) -> None:
    """Attach the filter to every handler of ``logger`` (root by default).

    Filters on handlers (not loggers) also see records propagated from child loggers.
    """
    target = logger or logging.getLogger()
    if not target.handlers:
        target.addHandler(logging.StreamHandler())
    for handler in target.handlers:
        if not any(isinstance(f, RedactingFilter) for f in handler.filters):
            handler.addFilter(RedactingFilter())
