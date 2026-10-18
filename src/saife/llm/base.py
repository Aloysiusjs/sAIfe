"""Provider-neutral LLM interface.

Clients either return a usable result or raise an ``LLMError`` subclass.
They never return a half-result: a refusal, truncation or unparseable output
always raises, so callers cannot mistake a failure for an answer.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, TypeVar

from pydantic import BaseModel, Field

StopReason = Literal["end_turn", "max_tokens", "refusal", "tool_use", "pause_turn", "other"]

T = TypeVar("T", bound=BaseModel)

# Messages are plain {"role": "user" | "assistant", "content": str} dicts.
Message = dict[str, Any]


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0


class TextResult(BaseModel):
    text: str
    stop_reason: StopReason
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    request_id: str | None = None


class ParsedResult[M: BaseModel](BaseModel):
    parsed: M
    raw_text: str
    stop_reason: StopReason
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    request_id: str | None = None


class LLMClient(Protocol):
    provider: str

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> TextResult: ...

    async def parse(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        schema: type[T],
        max_tokens: int = 16000,
        temperature: float | None = None,
    ) -> ParsedResult[T]: ...


class SearchHit(BaseModel):
    """One web page the search returned, with the readable text the API gave us for it.

    ``web_search_result`` blocks carry only ``encrypted_content`` (opaque to us), so
    ``text`` is built from the ``cited_text`` of citations that point at this URL.
    Those passages come from the page itself, not from the model's prose.
    """

    url: str
    title: str = ""
    text: str = ""
    page_age: str | None = None


class SearchResult(BaseModel):
    hits: list[SearchHit] = Field(default_factory=list)
    text: str = ""  # the model's own summary: never evidence
    stop_reason: StopReason
    usage: Usage = Field(default_factory=Usage)
    provider: str
    model: str
    request_id: str | None = None
    continuations: int = 0  # pause_turn resumptions


class SearchClient(Protocol):
    """Kept apart from ``LLMClient`` so verifier clients need not implement search."""

    provider: str

    async def search(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_uses: int,
        max_tokens: int = 16000,
    ) -> SearchResult: ...
