"""Offline LLM doubles for tests (PRD N3: tests are deterministic and free).

- ``FakeLLM``: answers from recorded fixtures on disk, or from a handler function.
- ``RecordingLLM``: wraps a real client and writes each response as a fixture.

Fixture key = sha256 of the canonical request (provider, model, kind, system,
messages, schema name, temperature). A missing fixture is an error, never a
silent default.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal, cast

from pydantic import BaseModel, ValidationError

from saife import errors
from saife.errors import FixtureMissingError, LLMError, LLMOutputError
from saife.llm.base import (
    LLMClient,
    Message,
    ParsedResult,
    SearchClient,
    SearchHit,
    SearchResult,
    StopReason,
    T,
    TextResult,
    Usage,
)

Kind = Literal["complete", "parse", "search"]


class FakeRequest(BaseModel):
    provider: str
    model: str
    kind: Kind
    system: str
    messages: list[Message]
    schema_name: str | None = None
    temperature: float | None = None
    max_uses: int | None = None  # search only

    def key(self) -> str:
        # Leave max_uses out when unset so keys of complete/parse fixtures are unchanged.
        data = self.model_dump(exclude={"max_uses"} if self.max_uses is None else None)
        canonical = json.dumps(data, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24]


class FakeResponse(BaseModel):
    text: str = ""
    stop_reason: StopReason = "end_turn"
    usage: Usage = Usage()
    model: str | None = None
    hits: list[SearchHit] = []  # search only
    # Simulate a failure: name of an saife.errors.LLMError subclass + message.
    error: str | None = None
    error_message: str = "simulated failure"


Handler = Callable[[FakeRequest], FakeResponse]

_ERRORS: dict[str, type[LLMError]] = {
    cls.__name__: cls
    for cls in vars(errors).values()
    if isinstance(cls, type) and issubclass(cls, LLMError)
}


def _raise_if_error(resp: FakeResponse, req: FakeRequest) -> None:
    if resp.error:
        cls = _ERRORS.get(resp.error)
        if cls is None:
            raise ValueError(f"unknown simulated error {resp.error!r}")
        raise cls(resp.error_message, provider=req.provider, model=req.model)


class FakeLLM:
    def __init__(
        self,
        provider: str,
        *,
        fixtures_dir: Path | None = None,
        handler: Handler | None = None,
    ) -> None:
        if fixtures_dir is None and handler is None:
            raise ValueError("FakeLLM needs fixtures_dir or handler")
        self.provider = provider
        self.fixtures_dir = fixtures_dir
        self.handler = handler
        self.calls: list[FakeRequest] = []

    def _respond(self, req: FakeRequest) -> FakeResponse:
        self.calls.append(req)
        if self.handler is not None:
            resp = self.handler(req)
        else:
            assert self.fixtures_dir is not None
            path = self.fixtures_dir / f"{req.key()}.json"
            if not path.exists():
                preview = req.messages[-1]["content"][:80] if req.messages else ""
                raise FixtureMissingError(
                    f"no fixture {path.name} for {req.provider}/{req.model} {req.kind} "
                    f"(last message: {preview!r}). Re-run the test with SAIFE_RECORD=1."
                )
            resp = FakeResponse.model_validate(json.loads(path.read_text("utf-8"))["response"])
        _raise_if_error(resp, req)
        return resp

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> TextResult:
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="complete",
            system=system,
            messages=messages,
            temperature=temperature,
        )
        resp = self._respond(req)
        return TextResult(
            text=resp.text,
            stop_reason=resp.stop_reason,
            usage=resp.usage,
            provider=self.provider,
            model=resp.model or model,
        )

    async def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        max_tokens: int = 16000,
        temperature: float | None = None,
    ) -> ParsedResult[T]:
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="parse",
            system=system,
            messages=messages,
            schema_name=schema.__name__,
            temperature=temperature,
        )
        resp = self._respond(req)
        try:
            parsed = schema.model_validate_json(resp.text)
        except ValidationError as exc:
            raise LLMOutputError(str(exc), provider=self.provider, model=model) from exc
        return ParsedResult[schema](  # type: ignore[valid-type]
            parsed=parsed,
            raw_text=resp.text,
            stop_reason=resp.stop_reason,
            usage=resp.usage,
            provider=self.provider,
            model=resp.model or model,
        )

    async def search(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_uses: int,
        max_tokens: int = 16000,
    ) -> SearchResult:
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="search",
            system=system,
            messages=messages,
            max_uses=max_uses,
        )
        resp = self._respond(req)
        return SearchResult(
            hits=resp.hits,
            text=resp.text,
            stop_reason=resp.stop_reason,
            usage=resp.usage,
            provider=self.provider,
            model=resp.model or model,
        )


class RecordingLLM:
    """Pass-through to a real client that saves each successful response as a fixture."""

    def __init__(self, inner: LLMClient, fixtures_dir: Path) -> None:
        self.inner = inner
        self.provider = inner.provider
        self.fixtures_dir = fixtures_dir
        fixtures_dir.mkdir(parents=True, exist_ok=True)

    def _save(self, req: FakeRequest, resp: FakeResponse) -> None:
        payload: dict[str, Any] = {"request": req.model_dump(), "response": resp.model_dump()}
        path = self.fixtures_dir / f"{req.key()}.json"
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> TextResult:
        result = await self.inner.complete(
            model=model,
            system=system,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="complete",
            system=system,
            messages=messages,
            temperature=temperature,
        )
        self._save(
            req,
            FakeResponse(
                text=result.text,
                stop_reason=result.stop_reason,
                usage=result.usage,
                model=result.model,
            ),
        )
        return result

    async def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        max_tokens: int = 16000,
        temperature: float | None = None,
    ) -> ParsedResult[T]:
        result = await self.inner.parse(
            model=model,
            system=system,
            messages=messages,
            schema=schema,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="parse",
            system=system,
            messages=messages,
            schema_name=schema.__name__,
            temperature=temperature,
        )
        # Store the validated object, not raw text, so replays always validate.
        self._save(
            req,
            FakeResponse(
                text=result.parsed.model_dump_json(),
                stop_reason=result.stop_reason,
                usage=result.usage,
                model=result.model,
            ),
        )
        return result

    async def search(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_uses: int,
        max_tokens: int = 16000,
    ) -> SearchResult:
        inner = cast(SearchClient, self.inner)
        result = await inner.search(
            model=model,
            system=system,
            messages=messages,
            max_uses=max_uses,
            max_tokens=max_tokens,
        )
        req = FakeRequest(
            provider=self.provider,
            model=model,
            kind="search",
            system=system,
            messages=messages,
            max_uses=max_uses,
        )
        self._save(
            req,
            FakeResponse(
                text=result.text,
                hits=result.hits,
                stop_reason=result.stop_reason,
                usage=result.usage,
                model=result.model,
            ),
        )
        return result
