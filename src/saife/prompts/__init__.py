"""Prompt templates (``*.md`` in this package) and the untrusted-data wrapper (P7)."""

from __future__ import annotations

import html
from functools import cache
from importlib import resources


@cache
def load(name: str) -> str:
    """Return the text of ``prompts/<name>.md``."""
    return resources.files(__package__).joinpath(f"{name}.md").read_text(encoding="utf-8")


def data_block(tag: str, text: str, **attrs: str) -> str:
    """Wrap untrusted text as a tagged data block.

    The content is HTML-escaped, so a source or draft cannot close the block or
    open a fake one (``</source>`` becomes ``&lt;/source&gt;``). ``saife.evidence``
    unescapes quotes the model copies back and checks them against the *original*
    source text.
    """
    attr = "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in attrs.items())
    return f"<{tag}{attr}>\n{html.escape(text, quote=False)}\n</{tag}>"
