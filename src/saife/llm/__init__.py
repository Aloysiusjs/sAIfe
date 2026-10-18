"""LLM clients and routing."""

from __future__ import annotations

from saife.config import (
    ANTHROPIC,
    OPENROUTER,
    VERIFIER,
    VERIFIER_FALLBACK,
    FallbackPolicy,
    Role,
    Settings,
)
from saife.llm.anthropic import AnthropicClient
from saife.llm.base import LLMClient, ParsedResult, TextResult, Usage
from saife.llm.openai_compat import OpenAICompatClient
from saife.llm.router import Routed, Router

__all__ = [
    "AnthropicClient",
    "LLMClient",
    "OpenAICompatClient",
    "ParsedResult",
    "Routed",
    "Router",
    "TextResult",
    "Usage",
    "build_router",
    "openrouter_client",
]

# OpenRouter request extras. Reasoning off: reasoning models otherwise spend the whole
# output budget on hidden reasoning and return no text. require_parameters: structured
# requests only go to upstream providers that honour response_format.
OPENROUTER_EXTRA_BODY = {"reasoning": {"enabled": False}}
OPENROUTER_STRUCTURED_EXTRA_BODY = {"provider": {"require_parameters": True}}


def openrouter_client(settings: Settings) -> OpenAICompatClient:
    return OpenAICompatClient(
        api_key=settings.require_openrouter(),
        base_url=settings.openrouter_base_url,
        structured_mode="json_schema",
        timeout=90.0,
        provider=OPENROUTER,
        extra_body=OPENROUTER_EXTRA_BODY,
        structured_extra_body=OPENROUTER_STRUCTURED_EXTRA_BODY,
    )


def build_router(
    settings: Settings, *, policy: FallbackPolicy = FallbackPolicy.FALLBACK_AND_DISCLOSE
) -> Router:
    """Build the production router from settings.

    ``routing="verifier"`` (default): roles run on the OpenAI-compatible verifier, except
    those overridden with ``SAIFE_ROLE_<ROLE>``. No Anthropic client is created unless an
    override names it, and judges never fall back to Claude: verifier outages fail closed
    (UNVERIFIABLE) whatever the preset's policy. ``routing="split"``: Claude + verifier.
    An OpenRouter client exists only when some role is routed to it."""
    api_key, base_url = settings.require_verifier()
    roles = settings.roles()
    used = {ref.provider for ref in roles.values()}
    clients: dict[str, LLMClient] = {
        VERIFIER: OpenAICompatClient(
            api_key=api_key,
            base_url=base_url,
            structured_mode=settings.verifier_structured_mode,
        ),
    }
    if OPENROUTER in used:
        clients[OPENROUTER] = openrouter_client(settings)
    if settings.routing == "verifier":
        if ANTHROPIC in used:
            clients[ANTHROPIC] = AnthropicClient(api_key=settings.anthropic_api_key)
        # Self-fallback = no fallback (the router never falls back to the same model).
        return Router(clients, roles, policy=policy, fallback_ref=roles[Role.GROUNDING_JUDGE])
    clients[ANTHROPIC] = AnthropicClient(api_key=settings.anthropic_api_key)
    return Router(clients, roles, policy=policy, fallback_ref=VERIFIER_FALLBACK)
