"""Detector registry: the preset's detector keys → fresh detector instances.

Instances are built per run, because some detectors cache per-run state
(the consistency detector keeps its resamples across passes).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx2

from saife.config import GuardConfig
from saife.detectors.base import Detector, DetectorContext

# Async DNS hook (host -> addresses) for the deterministic detector; None = real DNS.
Resolver = Callable[[str], Awaitable[list[str]]]


def build_detectors(
    config: GuardConfig,
    *,
    http_client: httpx2.AsyncClient | None = None,
    resolver: Resolver | None = None,
) -> list[Detector]:
    from saife.detectors.consistency import ConsistencyDetector
    from saife.detectors.deterministic import DeterministicDetector
    from saife.detectors.grounding import GroundingDetector
    from saife.detectors.web_evidence import WebEvidenceDetector

    detectors: list[Detector] = []
    for key in dict.fromkeys(config.detectors):
        if key == "grounding":
            detectors.append(GroundingDetector())
        elif key == "consistency":
            detectors.append(ConsistencyDetector())
        elif key == "web_evidence":
            detectors.append(WebEvidenceDetector())
        elif key == "deterministic":
            detectors.append(DeterministicDetector(http_client=http_client, resolver=resolver))
    return detectors


__all__ = ["Detector", "DetectorContext", "Resolver", "build_detectors"]
