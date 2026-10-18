"""Role → model routing, per-provider concurrency limits and the verifier fallback rule.

Fallback (PRD decision 1b) applies only to verifier roles, and only when the
verifier provider is *unavailable* (``LLMUnavailableError``). Refusals,
truncation and bad output are not outages. They propagate, and the claim
becomes UNVERIFIABLE.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from typing import cast

from pydantic import BaseModel

from saife.config import VERIFIER_FALLBACK, VERIFIER_ROLES, FallbackPolicy, ModelRef, Role
from saife.errors import ConfigError, LLMUnavailableError
from saife.llm.base import (
    LLMClient,
    Message,
    ParsedResult,
    SearchClient,
    SearchResult,
    T,
    TextResult,
)

log = logging.getLogger(__name__)


class Fallback(BaseModel):
    role: Role
    from_ref: ModelRef
    to_ref: ModelRef
    reason: str


class Routed[R: BaseModel](BaseModel):
    result: R
    ref: ModelRef  # the model that actually served the call
    fallback: Fallback | None = None


# OpenRouter free models allow 20 requests per minute, so keep its fan-out small.
DEFAULT_CONCURRENCY = {"anthropic": 8, "verifier": 4, "openrouter": 2}


class Router:
    def __init__(
        self,
        clients: Mapping[str, LLMClient],
        roles: Mapping[Role, ModelRef],
        *,
        policy: FallbackPolicy = FallbackPolicy.FALLBACK_AND_DISCLOSE,
        fallback_ref: ModelRef = VERIFIER_FALLBACK,
        concurrency: Mapping[str, int] | None = None,
    ) -> None:
        missing = {ref.provider for ref in roles.values()} - set(clients)
        if missing:
            raise ConfigError(f"no client configured for provider(s): {sorted(missing)}")
        self.clients = dict(clients)
        self.roles = dict(roles)
        self.policy = policy
        self.fallback_ref = fallback_ref
        limits = {**DEFAULT_CONCURRENCY, **(concurrency or {})}
        self._sems = {p: asyncio.Semaphore(limits.get(p, 4)) for p in self.clients}
        self.fallbacks: list[Fallback] = []

    @property
    def verifier_diversity(self) -> bool:
        """True when no verifier call so far had to fall back to the generator's provider."""
        return not self.fallbacks

    async def _call[R: BaseModel](
        self, role: Role, call: Callable[[LLMClient, str], Awaitable[R]]
    ) -> Routed[R]:
        ref = self.roles[role]
        try:
            async with self._sems[ref.provider]:
                result = await call(self.clients[ref.provider], ref.model)
            return Routed(result=result, ref=ref)
        except LLMUnavailableError as exc:
            can_fall_back = (
                role in VERIFIER_ROLES
                and self.policy is FallbackPolicy.FALLBACK_AND_DISCLOSE
                and self.fallback_ref != ref
                and self.fallback_ref.provider in self.clients
            )
            if not can_fall_back:
                raise
            fb = Fallback(role=role, from_ref=ref, to_ref=self.fallback_ref, reason=str(exc))
            self.fallbacks.append(fb)
            log.warning(
                "verifier unavailable for %s; falling back to %s (%s)",
                role,
                self.fallback_ref.model,
                exc,
            )
            async with self._sems[self.fallback_ref.provider]:
                result = await call(
                    self.clients[self.fallback_ref.provider], self.fallback_ref.model
                )
            return Routed(result=result, ref=self.fallback_ref, fallback=fb)

    async def complete(
        self,
        role: Role,
        *,
        system: str,
        messages: list[Message],
        max_tokens: int = 4096,
        temperature: float | None = None,
    ) -> Routed[TextResult]:
        return await self._call(
            role,
            lambda c, m: c.complete(
                model=m,
                system=system,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    async def parse(
        self,
        role: Role,
        *,
        system: str,
        messages: list[Message],
        schema: type[T],
        max_tokens: int = 16000,
        temperature: float | None = None,
    ) -> Routed[ParsedResult[T]]:
        return await self._call(
            role,
            lambda c, m: c.parse(
                model=m,
                system=system,
                messages=messages,
                schema=schema,
                max_tokens=max_tokens,
                temperature=temperature,
            ),
        )

    def can_search(self, role: Role) -> bool:
        """Whether the client serving ``role`` offers web search."""
        ref = self.roles.get(role)
        client = self.clients.get(ref.provider) if ref else None
        return callable(getattr(client, "search", None))

    async def search(
        self,
        role: Role,
        *,
        system: str,
        messages: list[Message],
        max_uses: int,
        max_tokens: int = 16000,
    ) -> Routed[SearchResult]:
        """Web search never falls back: it is not a verifier role, and any failure
        propagates so the claim becomes UNVERIFIABLE."""
        ref = self.roles[role]
        client = self.clients[ref.provider]
        if not callable(getattr(client, "search", None)):
            raise ConfigError(f"provider {ref.provider!r} cannot serve {role} (no search)")
        searcher = cast(SearchClient, client)
        async with self._sems[ref.provider]:
            result = await searcher.search(
                model=ref.model,
                system=system,
                messages=messages,
                max_uses=max_uses,
                max_tokens=max_tokens,
            )
        return Routed(result=result, ref=ref)
