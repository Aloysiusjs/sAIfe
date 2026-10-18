"""Terminal output that survives legacy Windows code pages.

A default Windows console is cp1252, which cannot encode the arrows and status
icons sAIfe prints; rich then raises UnicodeEncodeError mid-run. Switch the
standard streams to UTF-8 (replacing anything unencodable) before printing.
"""

from __future__ import annotations

import sys

from rich.console import Console


def utf8_console() -> Console:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None and (stream.encoding or "").lower() not in ("utf-8", "utf8"):
            reconfigure(encoding="utf-8", errors="replace")
    return Console()
