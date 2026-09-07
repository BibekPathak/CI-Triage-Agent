"""LLM provider factory and re-exports."""

from __future__ import annotations

from collections.abc import Callable

from app.config import settings
from app.llm.base import LLMProvider
from app.llm.deterministic import DeterministicLLM
from app.llm.openai import OpenAIProvider

__all__ = ["LLMProvider", "DeterministicLLM", "OpenAIProvider", "build_provider"]


def build_provider(
    *,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    structured_handler: Callable | None = None,
    complete_handler: Callable | None = None,
) -> LLMProvider:
    """Build the configured LLM provider.

    ``provider`` is one of ``openai`` or ``deterministic`` (defaults to
    ``settings.llm_provider``). The deterministic provider optionally accepts
    scripted handlers for reproducible tests/demo.
    """
    name = (provider or settings.llm_provider).lower()
    if name == "openai":
        return OpenAIProvider(api_key=api_key, model=model)
    if name in {"deterministic", "mock", "offline"}:
        return DeterministicLLM(
            structured_handler=structured_handler,
            complete_handler=complete_handler,
        )
    raise ValueError(f"Unknown LLM provider: {name!r}")
