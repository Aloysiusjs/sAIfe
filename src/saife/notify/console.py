"""One readable console line per event.

Statuses always carry an icon and a text label as well as a colour (PRD N5), so
the output still reads in a monochrome terminal or a log file. Untrusted text is
appended as ``Text`` segments, never parsed as rich markup.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.text import Text

from saife.types import Event

# status -> (icon, style); the label is the status name itself.
STATUS_STYLE: dict[str, tuple[str, str]] = {
    "SUPPORTED": ("✓", "green"),
    "CONTRADICTED": ("✗", "bold red"),
    "LIKELY": ("~", "cyan"),
    "UNSUPPORTED": ("?", "yellow"),
    "UNVERIFIABLE": ("!", "magenta"),
    "VERIFIED": ("✓", "bold green"),
    "CAUTION": ("!", "bold yellow"),
    "BLOCKED": ("✗", "bold red"),
}
SEVERITY_STYLE: dict[str, tuple[str, str]] = {
    "info": ("i", "blue"),
    "warning": ("!", "yellow"),
    "high": ("✗", "bold red"),
}
_MAX_TEXT = 90


def _clip(text: str, limit: int = _MAX_TEXT) -> str:
    flat = " ".join(text.split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def status_label(status: str) -> Text:
    icon, style = STATUS_STYLE.get(status, ("•", ""))
    return Text(f"{icon} {status}", style=style)


def _get(data: dict[str, Any], *path: str) -> Any:
    cur: Any = data
    for key in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def format_event(event: Event) -> Text:
    d = event.data
    line = Text(f"[{event.seq:>3}] ", style="dim")
    match event.type:
        case "run.started":
            line.append("▶ run started ", style="bold")
            line.append(f"preset={d.get('preset')} mode={d.get('mode')}")
            if d.get("question"):
                line.append(f" · {_clip(str(d['question']))}")
        case "pass.started":
            line.append(f"» pass {d.get('index')} started", style="bold")
        case "claim.extracted":
            line.append("• claim ")
            line.append(_clip(str(_get(d, "claim", "text") or "")))
        case "claim.verdict":
            line.append_text(status_label(str(_get(d, "result", "status"))))
            risk = _get(d, "result", "risk")
            if isinstance(risk, int | float):
                line.append(f" risk={risk:.2f}", style="dim")
            line.append(" ")
            line.append(_clip(str(_get(d, "result", "claim", "text") or "")))
        case "repair.applied":
            op = str(_get(d, "repair", "op") or "repair")
            line.append(f"✎ {op.upper()} ", style="bold blue")
            line.append(_clip(str(_get(d, "repair", "before") or ""), 40))
            line.append(" → ")
            line.append(_clip(str(_get(d, "repair", "after") or "(removed)"), 40))
        case "alert.raised":
            severity = str(_get(d, "alert", "severity"))
            icon, style = SEVERITY_STYLE.get(severity, ("•", ""))
            line.append(f"{icon} ALERT {severity.upper()} ", style=style)
            line.append(_clip(str(_get(d, "alert", "message") or "")))
        case "run.completed":
            line.append("■ run completed ", style="bold")
            line.append_text(status_label(str(d.get("status"))))
            risk = _get(d, "report", "risk")
            if isinstance(risk, int | float):
                line.append(f" risk={risk:.2f}", style="dim")
        case "run.failed":
            line.append("✗ RUN FAILED ", style="bold red")
            line.append(_clip(str(d.get("error") or "")))
    return line


class ConsoleSink:
    def __init__(self, console: Console) -> None:
        self._console = console

    async def __call__(self, event: Event) -> None:
        self._console.print(format_event(event), soft_wrap=True)
