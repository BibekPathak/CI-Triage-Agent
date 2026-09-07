"""LLM provider abstraction.

The application depends only on the :class:`LLMProvider` protocol, never on a
specific vendor SDK. Implementations provide:

* :meth:`complete` - free-form text completion.
* :meth:`structured` - completion constrained to a Pydantic model (returns the
  parsed instance).

Both record token usage and estimated cost on a provided ``cost_sink`` so the
agent can enforce budgets without leaking raw billing internals.

Providers:

* :class:`OpenAIProvider` - native structured output / tool calling via the
  ``openai`` SDK.
* :class:`DeterministicLLM` - scripted, offline responses used by unit tests and
  the deterministic demo (no live model needed).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

_T = TypeVar("_T", bound=BaseModel)

# A lightweight representation of a chat message list. Backend providers may
# need to cast this to their SDK-specific wire type.
Messages = list[dict[str, str]]

# A cost sink is any object accepting records of token usage. TriageState
# already has ``record_cost``; other sinks only need the same signature.
CostSink = Callable[[int, int, float, float], None]


class LLMProvider(Protocol):
    """Uniform interface over an LLM backend."""

    provider_name: str

    async def complete(
        self,
        messages: Messages,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cost_sink: CostSink | None = None,
    ) -> str:
        """Return the model's text completion for ``messages``."""
        ...

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
        """Return a Pydantic instance of ``model`` parsed from model output."""
        ...
