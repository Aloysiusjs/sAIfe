"""Shared fakes for the invariant and red-team suites.

Runs go through the real ``Guard``/``Pipeline``, decomposer, detectors and repair. Only
the models (``FakeLLM`` handlers driven by a ``World``), HTTP (``MockTransport``) and DNS
are faked, so nothing touches the network.
"""

from __future__ import annotations

import asyncio
import html
import json
import random
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx2

from saife import Guard
from saife.config import ANTHROPIC, VERIFIER, GuardConfig, default_roles, get_preset
from saife.events import EventBus
from saife.llm.base import SearchHit, Usage
from saife.llm.fake import FakeLLM, FakeRequest, FakeResponse
from saife.llm.router import Router
from saife.trace import load_trace
from saife.types import Event, RepairRecord, Report

PARIS = "The Eiffel Tower stands on the Champ de Mars in Paris."
BUILT = "It was built between 1887 and 1889 for the World's Fair."
HEIGHT = "The tower is 330 metres tall, including its antennas."
ENGINEER = "It was designed by the company of engineer Gustave Eiffel."
PAINT = "The tower is repainted roughly every seven years."

SOURCE_TEXT = "\n\n".join([PARIS, BUILT, HEIGHT, ENGINEER, PAINT])

# Sentences a draft may contain: true ones (in the source) and false ones (not in it).
TRUE_SENTENCES = [PARIS, BUILT, HEIGHT, ENGINEER, PAINT]
FALSE_SENTENCES = [
    "It was completed in 1901 for the World's Fair.",
    "The tower is painted gold every year.",
    "It is 900 metres tall.",
    "It was designed by Thomas Edison.",
    "The tower stands in Lyon.",
]
FABRICATED_QUOTES = [
    "The Eiffel Tower was completed in 1901",
    "The tower is 900 metres tall",
    "painted gold every single year",
    "designed by Thomas Edison himself",
    "The tower is 330 metres tall, excluding",  # near miss
    "Champ de Mars in Lyon, France",
]

_SENTENCE = re.compile(r"[^.!?\n]+[.!?]")


def unblock(raw: str) -> str:
    """Contents of a ``data_block`` (strip the wrapper newlines, undo the escaping)."""
    if raw.startswith("\n"):
        raw = raw[1:]
    if raw.endswith("\n"):
        raw = raw[:-1]
    return html.unescape(raw)


def blocks(content: str, tag: str) -> list[tuple[dict[str, str], str]]:
    """Every ``<tag attrs>…</tag>`` block in a prompt as (attrs, unescaped text).

    Parsed from the raw (escaped) prompt, so injected tags inside the data can't
    create or close a block."""
    out = []
    for m in re.finditer(rf"<{tag}((?: [a-z_]+=\"[^\"]*\")*)>(.*?)</{tag}>", content, re.DOTALL):
        attrs = {k: html.unescape(v) for k, v in re.findall(r' ([a-z_]+)="([^"]*)"', m.group(1))}
        out.append((attrs, unblock(m.group(2))))
    return out


def user(req: FakeRequest) -> str:
    return str(req.messages[-1]["content"])


Judge = Callable[[str, dict[str, str]], dict[str, Any]]
Repairer = Callable[[list[dict[str, Any]]], list[dict[str, Any]]]
Searcher = Callable[[str], list[SearchHit]]
WebJudge = Callable[[str, dict[str, str]], dict[str, Any]]
SampleJudge = Callable[[str, list[str]], dict[str, Any]]


def honest_judge(claim: str, sources: dict[str, str]) -> dict[str, Any]:
    for sid, text in sources.items():
        if claim in text:
            quotes = [{"source_id": sid, "quote": claim}]
            return {"verdict": "SUPPORTED", "confidence": 0.9, "quotes": quotes}
    return {"verdict": "NOT_FOUND", "confidence": 0.6, "quotes": []}


def no_hits(claim: str) -> list[SearchHit]:
    return []


def web_not_found(claim: str, hits: dict[str, str]) -> dict[str, Any]:
    return {"verdict": "NOT_FOUND", "confidence": 0.5}


def samples_absent(claim: str, sample_ids: list[str]) -> dict[str, Any]:
    return {"verdicts": [{"sample_id": i, "verdict": "absent"} for i in sample_ids]}


def no_error(req: FakeRequest) -> str | None:
    return None


def remove_all(flagged: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{"claim_id": f["id"], "op": "remove", "reason": "unsupported"} for f in flagged]


@dataclass
class World:
    """Scripted model behaviour. Each hook may raise or return an error response."""

    judge: Judge = honest_judge
    repair: Repairer = remove_all
    generated: str = f"{PARIS} {HEIGHT}"
    search: Searcher = no_hits
    web_judge: WebJudge = web_not_found
    consistency: SampleJudge = samples_absent
    usage: Usage = field(default_factory=Usage)
    claude_error: Callable[[FakeRequest], str | None] = no_error
    verifier_error: Callable[[FakeRequest], str | None] = no_error

    def claude(self, req: FakeRequest) -> FakeResponse:
        if err := self.claude_error(req):
            return FakeResponse(error=err)
        if req.schema_name == "DecomposeOut":
            (_, draft), *_ = blocks(user(req), "draft")
            claims = [
                {"text": s.strip(), "anchor": s.strip(), "kind": "entity", "checkworthy": True}
                for s in _SENTENCE.findall(draft)
            ]
            return self._json({"claims": claims})
        if req.schema_name == "RepairOut":
            return self._json({"edits": self.repair(parse_flagged(user(req)))})
        if req.kind == "search":
            (_, claim), *_ = blocks(user(req), "claim")
            return FakeResponse(hits=self.search(claim), usage=self.usage)
        if req.kind == "complete":
            return FakeResponse(text=self.generated, usage=self.usage)
        return self.verifier(req)  # fallback judge calls

    def verifier(self, req: FakeRequest) -> FakeResponse:
        if err := self.verifier_error(req):
            return FakeResponse(error=err)
        content = user(req)
        (_, claim), *_ = blocks(content, "claim")
        if req.schema_name == "GroundingJudgement":
            sources = {a["id"]: t for a, t in blocks(content, "source")}
            return self._json(self.judge(claim, sources))
        if req.schema_name == "WebJudgement":
            hits = {a["id"]: t for a, t in blocks(content, "search_result")}
            return self._json(self.web_judge(claim, hits))
        if req.schema_name == "ConsistencyJudgement":
            ids = [a["id"] for a, _ in blocks(content, "sample")]
            return self._json(self.consistency(claim, ids))
        raise AssertionError(f"unexpected verifier call {req.schema_name}")

    def _json(self, data: dict[str, Any]) -> FakeResponse:
        return FakeResponse(text=json.dumps(data), usage=self.usage)


def parse_flagged(content: str) -> list[dict[str, Any]]:
    flagged = []
    evidence = blocks(content, "evidence")
    for attrs, text in blocks(content, "claim"):
        flagged.append(
            {
                "id": attrs["id"],
                "status": attrs["status"],
                "text": text,
                "evidence": [t for a, t in evidence if a["claim_id"] == attrs["id"]],
            }
        )
    return flagged


class SlowFakeLLM(FakeLLM):
    """A FakeLLM whose calls sleep first (async), so ``asyncio.timeout`` can cut them."""

    def __init__(self, provider: str, *, delay: float, slow: Callable[[str], bool], **kw: Any):
        super().__init__(provider, **kw)
        self.delay = delay
        self.slow = slow  # schema name or kind -> sleep?

    async def parse(self, **kw: Any) -> Any:
        if self.slow(kw["schema"].__name__):
            await asyncio.sleep(self.delay)
        return await super().parse(**kw)

    async def complete(self, **kw: Any) -> Any:
        if self.slow("complete"):
            await asyncio.sleep(self.delay)
        return await super().complete(**kw)


def make_router(
    world: World, *, claude: FakeLLM | None = None, verifier: FakeLLM | None = None
) -> Router:
    c = claude or FakeLLM(ANTHROPIC, handler=world.claude)
    v = verifier or FakeLLM(VERIFIER, handler=world.verifier)
    return Router({ANTHROPIC: c, VERIFIER: v}, default_roles())


async def fake_resolver(host: str) -> list[str]:
    return ["93.184.216.34"]


def http_client() -> httpx2.AsyncClient:
    return httpx2.AsyncClient(transport=httpx2.MockTransport(lambda r: httpx2.Response(200)))


def config(preset: str = "balanced", **overrides: Any) -> GuardConfig:
    cfg = get_preset(preset)
    return cfg.with_overrides(**overrides) if overrides else cfg


@dataclass
class Outcome:
    report: Report | None
    events: list[Event]
    trace: list[Event]
    error: BaseException | None = None


async def run_guard(
    tmp_path: Path,
    world: World,
    *,
    answer: str | None = None,
    question: str | None = None,
    sources: list[Any] | None = None,
    cfg: GuardConfig | str = "balanced",
    router: Router | None = None,
    run_id: str = "run0000000001",
) -> Outcome:
    """One run through ``Guard``; exceptions are captured, not raised."""
    events: list[Event] = []

    async def sink(event: Event) -> None:
        events.append(event)

    g = Guard(
        cfg,
        router=router or make_router(world),
        trace_dir=tmp_path / "runs",
        http_client=http_client(),
        resolver=fake_resolver,
    )
    report: Report | None = None
    error: BaseException | None = None
    try:
        if answer is not None:
            report = await g.check(
                answer, sources=sources, question=question, run_id=run_id, extra_sinks=[sink]
            )
        else:
            assert question is not None
            report = await g.run(question, sources=sources, run_id=run_id, extra_sinks=[sink])
    except Exception as exc:
        error = exc
    trace_file = tmp_path / "runs" / f"{run_id}.jsonl"
    trace = load_trace(trace_file) if trace_file.exists() else []
    return Outcome(report=report, events=events, trace=trace, error=error)


def terminal_events(events: list[Event]) -> list[Event]:
    return [e for e in events if e.type in ("run.completed", "run.failed")]


def assert_one_terminal(events: list[Event]) -> None:
    ends = terminal_events(events)
    assert len(ends) == 1, [e.type for e in events]
    assert events[-1] is ends[0] or events[-1] == ends[0]
    assert events[0].type == "run.started"


def bus_events(bus: EventBus) -> list[Event]:
    return list(bus.history)


def invert_pass(after: str, records: list[RepairRecord]) -> str:
    """Undo one pass of repairs: records' spans are offsets in the pre-repair draft."""
    out: list[str] = []
    pos = 0  # in `after`
    delta = 0  # len(after) - len(before) so far
    for r in sorted(records, key=lambda x: x.span[0]):
        s, e = r.span
        assert e - s == len(r.before), r
        ns = s + delta
        assert after[ns : ns + len(r.after)] == r.after, (after, r)
        out += [after[pos:ns], r.before]
        pos = ns + len(r.after)
        delta += len(r.after) - len(r.before)
    out.append(after[pos:])
    return "".join(out)


def seeded(seed: int) -> random.Random:
    """Reproducible randomness for scripted runs (not used for anything secret)."""
    return random.Random(seed)  # noqa: S311
