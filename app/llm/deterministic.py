"""Deterministic LLM provider for tests and the offline demo.

This provider produces scripted, reproducible output with NO live API calls. It
is used by the unit/agent tests (so tests never depend on a model) and by
``make demo`` so the demo runs fully offline.

Callers may supply a ``structured_handler`` function that maps
``(model, user_prompt)`` to a dict; similarly a ``complete_handler`` maps
``(user_prompt)`` to text. If no handler is provided the provider returns an
empty/neutral response built from the target model.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, TypeAdapter

from app.llm.base import CostSink

_T = TypeVar("_T", bound=BaseModel)


def _default_structured[TModel: BaseModel](model: type[TModel], user: str) -> dict[str, Any]:
    """Neutral default: empty strings, zero confidence, empty lists."""
    return {
        "root_cause": "",
        "evidence": [],
        "confidence": 0.0,
        "next_action": "",
        "reasoning_summary": "",
        "description": "",
        "steps": [],
        "hypotheses": [],
        "plan": [],
        "title": "",
        "body": "",
    }


class DeterministicLLM:
    """Scripted LLM provider. No network. Fully reproducible."""

    provider_name = "deterministic"

    def __init__(
        self,
        *,
        structured_handler: Callable[[type, str], dict[str, Any]] | None = None,
        complete_handler: Callable[[str], str] | None = None,
    ) -> None:
        self._structured_handler = structured_handler or _default_structured
        self._complete_handler = complete_handler or (lambda user: "ok")

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cost_sink: CostSink | None = None,
    ) -> str:
        user = "\n".join(m.get("content", "") for m in messages if m.get("role") == "user")
        return self._complete_handler(user)

    async def structured(
        self,
        model: type[_T],
        system: str,
        user: str,
        *,
        temperature: float = 0.0,
        max_tokens: int | None = None,
        cost_sink: CostSink | None = None,
        extra_args: dict[str, Any] | None = None,
    ) -> _T:
        data = self._structured_handler(model, user)
        adapter: TypeAdapter[_T] = TypeAdapter(model)
        return adapter.validate_python(data)
