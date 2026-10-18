"""Claude client (generator, decomposer, web retrieval, repair)."""

from __future__ import annotations

import logging
from typing import Any, NoReturn

import anthropic
import httpx2

from saife.errors import (
    LLMError,
    LLMOutputError,
    LLMRefusalError,
    LLMTruncatedError,
    LLMUnavailableError,
)
from saife.llm.base import (
    Message,
    ParsedResult,
    SearchHit,
    SearchResult,
    StopReason,
    T,
    TextResult,
    Usage,
)

log = logging.getLogger(__name__)

PROVIDER = "anthropic"

# Server-side refusal fallbacks: "default" routes by refusal category.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
FALLBACK_MODELS = frozenset({"claude-opus-5", "claude-opus-5-5", "claude-fable-5-1"})

# Models that still accept temperature. Opus 5 / Sonnet 5 / Fable reject it with a 400.
_SAMPLING_PREFIXES = ("claude-haiku-4-5", "claude-sonnet-4-6", "claude-opus-4-6")

# The basic search tool, deliberately. The dynamic-filtering variant (web_search_20260209)
# runs searches inside code execution: page text reaches only the model's sandbox and the
# final answer carries no citations, so nothing readable is left to verify quotes against.
# The basic tool returns cited passages (``cited_text``) taken from the pages themselves.
WEB_SEARCH_TOOL = "web_search_20250305"
# Retrieval only searches and quotes; low effort cut latency ~8x and cost ~2x in live checks.
WEB_SEARCH_EFFORT = "low"
# pause_turn means the server-side tool loop hit its iteration limit; resume at most this often.
MAX_PAUSE_CONTINUATIONS = 3


def accepts_sampling(model: str) -> bool:
    return model.startswith(_SAMPLING_PREFIXES)


def _stop_reason(raw: str | None) -> StopReason:
    if raw in ("end_turn", "stop_sequence"):
        return "end_turn"
    if raw in ("max_tokens", "refusal", "tool_use", "pause_turn"):
        return raw  # type: ignore[return-value]
    return "other"


class AnthropicClient:
    provider = PROVIDER

    def __init__(
        self,
        *,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 2,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        # api_key=None lets the SDK resolve credentials (env var or `ant auth login` profile).
        kwargs: dict[str, Any] = {"timeout": timeout, "max_retries": max_retries}
        if api_key:
            kwargs["api_key"] = api_key
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = anthropic.AsyncAnthropic(**kwargs)

    def _common(
        self,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int,
        temperature: float | None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": messages,
        }
        if temperature is not None:
            if not accepts_sampling(model):
                raise ValueError(f"{model} does not accept temperature; omit it")
            # anthropic 1.x dropped sampling kwargs from its signatures, but the API still
            # honours them on older models. The consistency detector depends on
            # temperature, so send it through extra_body (the SDK's documented route).
            params["extra_body"] = {"temperature": temperature}
        if model in FALLBACK_MODELS:
            params["betas"] = [FALLBACK_BETA]
            params["fallbacks"] = "default"
        return params

    def _check_stop(self, stop: StopReason, model: str, response: Any) -> None:
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise LLMRefusalError(
                f"model refused (category={category})", provider=PROVIDER, model=model
            )
        if stop == "max_tokens":
            raise LLMTruncatedError("output hit max_tokens", provider=PROVIDER, model=model)

    def _map_error(self, exc: anthropic.APIError, model: str) -> NoReturn:
        if isinstance(exc, anthropic.BadRequestError):
            # A 400 is our bug (bad params), not an outage: never fall back on it.
            raise LLMError(f"bad request: {exc.message}", provider=PROVIDER, model=model) from exc
        raise LLMUnavailableError(
            f"{type(exc).__name__}: {getattr(exc, 'message', exc)}",
            provider=PROVIDER,
            model=model,
        ) from exc

    @staticmethod
    def _text(response: Any) -> str:
        return "".join(b.text for b in response.content if getattr(b, "type", None) == "text")

    @staticmethod
    def _usage(response: Any) -> Usage:
        u = response.usage
        return Usage(input_tokens=u.input_tokens or 0, output_tokens=u.output_tokens or 0)

    async def complete(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> TextResult:
        params = self._common(model, system, messages, max_tokens, temperature)
        try:
            if "betas" in params:
                response: Any = await self._client.beta.messages.create(**params)
            else:
                response = await self._client.messages.create(**params)
        except anthropic.APIError as exc:
            self._map_error(exc, model)
        stop = _stop_reason(response.stop_reason)
        self._check_stop(stop, model, response)
        return TextResult(
            text=self._text(response),
            stop_reason=stop,
            usage=self._usage(response),
            provider=PROVIDER,
            model=response.model,
            request_id=getattr(response, "_request_id", None),
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
        params = self._common(model, system, messages, max_tokens, temperature)
        params["output_format"] = schema
        try:
            if "betas" in params:
                response: Any = await self._client.beta.messages.parse(**params)
            else:
                response = await self._client.messages.parse(**params)
        except anthropic.APIError as exc:
            self._map_error(exc, model)
        except ValueError as exc:  # SDK-side schema validation failure
            raise LLMOutputError(
                f"invalid structured output: {exc}", provider=PROVIDER, model=model
            ) from exc
        stop = _stop_reason(response.stop_reason)
        self._check_stop(stop, model, response)
        parsed = response.parsed_output
        if parsed is None:
            raise LLMOutputError("no structured output returned", provider=PROVIDER, model=model)
        return ParsedResult[schema](  # type: ignore[valid-type]
            parsed=parsed,
            raw_text=self._text(response),
            stop_reason=stop,
            usage=self._usage(response),
            provider=PROVIDER,
            model=response.model,
            request_id=getattr(response, "_request_id", None),
        )

    async def _create(self, params: dict[str, Any], model: str) -> Any:
        try:
            if "betas" in params:
                return await self._client.beta.messages.create(**params)
            return await self._client.messages.create(**params)
        except anthropic.APIError as exc:
            self._map_error(exc, model)

    async def search(
        self,
        *,
        model: str,
        system: str,
        messages: list[Message],
        max_uses: int,
        max_tokens: int = 16000,
    ) -> SearchResult:
        """One turn with the server-side web search tool, resuming ``pause_turn``.

        Search errors arrive as HTTP 200 with an error object in the result block;
        they raise ``LLMUnavailableError`` so the claim fails closed.
        """
        tool = {"type": WEB_SEARCH_TOOL, "name": "web_search", "max_uses": max_uses}
        blocks: list[Any] = []
        usage = Usage()
        continuations = 0
        while True:
            convo = list(messages)
            if blocks:
                # Resume by replaying the paused assistant turn; no extra user message.
                convo.append({"role": "assistant", "content": [_dump(b) for b in blocks]})
            params = self._common(model, system, convo, max_tokens, None)
            params["tools"] = [tool]
            params["output_config"] = {"effort": WEB_SEARCH_EFFORT}
            response = await self._create(params, model)
            blocks.extend(response.content)
            u = self._usage(response)
            usage = Usage(
                input_tokens=usage.input_tokens + u.input_tokens,
                output_tokens=usage.output_tokens + u.output_tokens,
            )
            stop = _stop_reason(response.stop_reason)
            self._check_stop(stop, model, response)
            if stop != "pause_turn":
                break
            if continuations >= MAX_PAUSE_CONTINUATIONS:
                raise LLMTruncatedError(
                    f"web search still paused after {continuations} continuations",
                    provider=PROVIDER,
                    model=model,
                )
            continuations += 1
        return SearchResult(
            hits=_collect_hits(blocks, model),
            text="".join(b.text for b in blocks if getattr(b, "type", None) == "text"),
            stop_reason=stop,
            usage=usage,
            provider=PROVIDER,
            model=response.model,
            request_id=getattr(response, "_request_id", None),
            continuations=continuations,
        )


def _dump(block: Any) -> Any:
    if hasattr(block, "model_dump"):
        return block.model_dump(mode="json", exclude_none=True)
    return block


_TRUNCATED = "..."


def _passage(cited: str) -> str:
    """``cited_text`` with the API's trailing truncation marker removed.

    A quote that runs into the marker would match text the page never contained.
    """
    text = cited.strip()
    if text.endswith(_TRUNCATED):
        text = text[: -len(_TRUNCATED)].rstrip()
    return text


def _collect_hits(blocks: list[Any], model: str) -> list[SearchHit]:
    """Search results by URL, with readable text taken from citations' ``cited_text``.

    ``web_search_result`` blocks carry only ``encrypted_content``; a result the model
    did not cite keeps empty text and the detector drops it.
    """
    hits: dict[str, SearchHit] = {}
    passages: dict[str, list[str]] = {}
    errors: list[str] = []
    for block in blocks:
        kind = getattr(block, "type", None)
        if kind == "web_search_tool_result":
            content = block.content
            if not isinstance(content, list):  # an error object, not a result list
                errors.append(str(getattr(content, "error_code", "unknown")))
                continue
            for r in content:
                if getattr(r, "type", None) == "web_search_result" and r.url not in hits:
                    hits[r.url] = SearchHit(
                        url=r.url, title=r.title or "", page_age=getattr(r, "page_age", None)
                    )
        elif kind == "text":
            for c in getattr(block, "citations", None) or []:
                if getattr(c, "type", None) != "web_search_result_location":
                    continue
                if c.url not in hits:
                    hits[c.url] = SearchHit(url=c.url, title=getattr(c, "title", None) or "")
                seen = passages.setdefault(c.url, [])
                passage = _passage(c.cited_text or "")
                if passage and passage not in seen:
                    seen.append(passage)
    # The model may try one search too many; that is not an outage if others succeeded.
    fatal = [e for e in errors if e != "max_uses_exceeded" or not hits]
    if fatal:
        raise LLMUnavailableError(
            f"web search error: {', '.join(fatal)}", provider=PROVIDER, model=model
        )
    # Blank lines between passages keep each one its own paragraph for the evidence check.
    return [
        h.model_copy(update={"text": "\n\n".join(passages.get(url, []))}) for url, h in hits.items()
    ]
