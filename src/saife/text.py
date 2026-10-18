"""Text normalisation shared by claim IDs and the evidence quote check."""

from __future__ import annotations

import re
import unicodedata

_PUNCT = str.maketrans(
    {
        "‘": "'",
        "’": "'",
        "‚": "'",
        "‛": "'",
        "′": "'",
        "“": '"',
        "”": '"',
        "„": '"',
        "‟": '"',
        "″": '"',
        "‐": "-",
        "‑": "-",
        "‒": "-",
        "–": "-",
        "—": "-",
        "―": "-",
        "−": "-",
        " ": " ",
        "​": "",
        "‌": "",
        "‍": "",
        "﻿": "",
    }
)
_WS = re.compile(r"\s+")


def normalize(text: str) -> str:
    """NFKC, straight quotes and hyphens, zero-width characters removed, whitespace collapsed."""
    text = unicodedata.normalize("NFKC", text).translate(_PUNCT)
    return _WS.sub(" ", text).strip()
