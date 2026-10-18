"""sAIfe: a multi-pass hallucination guard for agentic AI."""

from __future__ import annotations

__version__ = "0.1.0"

from saife.config import PRESETS, GuardConfig, get_preset  # noqa: E402
from saife.guard import Guard  # noqa: E402
from saife.types import Report, Source  # noqa: E402

__all__ = ["PRESETS", "Guard", "GuardConfig", "Report", "Source", "__version__", "get_preset"]
