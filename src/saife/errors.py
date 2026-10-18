"""Typed exceptions.

Every LLM failure is raised as an ``LLMError`` subclass. Detectors catch
``LLMError`` and turn it into an ``UNVERIFIABLE`` verdict (fail closed, P1):
a failure must never be swallowed or read as support.
"""


class SaifeError(Exception):
    """Base class for all sAIfe errors."""


class ConfigError(SaifeError):
    """Missing or invalid configuration."""


class LLMError(SaifeError):
    """A model call did not produce a usable result."""

    def __init__(self, message: str, *, provider: str, model: str) -> None:
        super().__init__(message)
        self.provider = provider
        self.model = model


class LLMUnavailableError(LLMError):
    """The provider could not be reached or returned a server/auth/rate-limit error
    after retries. The router may fall back to another provider on this error."""


class LLMRefusalError(LLMError):
    """The model declined (Anthropic ``refusal``, OpenAI-compatible ``content_filter``)."""


class LLMTruncatedError(LLMError):
    """Output hit the token limit, so any structured output may be incomplete."""


class LLMOutputError(LLMError):
    """Output could not be parsed or validated against the expected schema."""


class FixtureMissingError(SaifeError):
    """FakeLLM has no recorded response for a request."""
