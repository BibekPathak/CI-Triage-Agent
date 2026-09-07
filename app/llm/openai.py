"""OpenAI-backed LLM provider using native structured output / function calling.

Requires the ``openai`` SDK and a valid ``OPENAI_API_KEY``. Structured requests
use the model's native ``response_format`` (JSON object) and JSON schema from the
target Pydantic model, then validate/parse the result.
"""

from __future__ import annotations

import json
from typing import Any, TypeVar, cast

from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionMessageParam
from pydantic import BaseModel, TypeAdapter

from app.config import settings
from app.llm.base import CostSink, Messages

_T = TypeVar("_T", bound=BaseModel)


class OpenAIProvider:
    """LLM provider backed by the OpenAI API."""

    provider_name = "openai"

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        *,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or settings.openai_api_key
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY is not set; cannot use OpenAIProvider")
        self.model = model or settings.model
        self.timeout = timeout or settings.llm_timeout_seconds
        self.client = AsyncOpenAI(api_key=self.api_key, timeout=self.timeout)

    def _record(self, usage: Any, cost_sink: CostSink | None) -> None:
        if cost_sink is None or usage is None:
            return
        est_in = getattr(usage, "prompt_tokens", 0) or 0
        est_out = getattr(usage, "completion_tokens", 0) or 0
        cost_sink(
            int(est_in),
            int(est_out),
            settings.est_cost_per_1k_in,
            settings.est_cost_per_1k_out,
        )

    async def complete(
        self,
        messages: Messages,
        *,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        cost_sink: CostSink | None = None,
    ) -> str:
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=cast(list[ChatCompletionMessageParam], messages),
            temperature=temperature,
            max_tokens=max_tokens or settings.max_tokens,
        )
        self._record(resp.usage, cost_sink)
        return resp.choices[0].message.content or ""

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
        schema = model.model_json_schema()
        resp = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
            max_tokens=max_tokens or settings.max_tokens,
            response_format={
                "type": "json_schema",
                "json_schema": {
                    "name": model.__name__,
                    "schema": schema,
                    "strict": True,
                },
            },
        )
        self._record(resp.usage, cost_sink)
        content = resp.choices[0].message.content or ""
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"LLM returned invalid JSON for {model.__name__}: {exc}") from exc
        adapter: TypeAdapter[_T] = TypeAdapter(model)
        return adapter.validate_python(data)
