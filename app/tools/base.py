"""Tool abstraction.

A :class:`Tool` is a typed, callable unit of capability exposed to the agent.
Each tool declares:

* a ``name``
* a Pydantic ``params`` model describing its required/optional arguments
* a human ``description`` (sent to the LLM)
* a security ``action_class``
* a synchronous ``run`` method returning a structured ``ToolResult``

Tools are registered in a :class:`ToolRegistry` so the orchestrator and the LLM
see a consistent, schema-driven surface.
"""

from __future__ import annotations

from typing import Any, ClassVar, TypeVar

from pydantic import BaseModel

from app.models.domain import ActionClass, ToolResult

_TP = TypeVar("_TP", bound=BaseModel)

# Sentinel type alias referenced in tool schemas for structured tool-calling.
ParamSchema = BaseModel


class Tool[TP: BaseModel](BaseModel):
    """Base class for all tools."""

    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    params: ClassVar[type[BaseModel] | None] = None
    action_class: ClassVar[ActionClass] = ActionClass.READ_ONLY

    def to_definition(self) -> dict[str, Any]:
        """A JSON-schema-ish definition suitable for LLM tool calling."""
        schema = self.params.model_json_schema() if self.params else {"type": "object"}
        return {
            "name": self.name,
            "description": self.description,
            "parameters": schema,
            "action_class": self.action_class.value,
        }

    def run(self, args: dict[str, Any]) -> ToolResult:  # pragma: no cover - abstract
        raise NotImplementedError

    def execute(self, args: dict[str, Any]) -> ToolResult:
        """Validate args against the params schema, then run."""
        if self.params is not None:
            validated = self.params.model_validate(args).model_dump()
            args = {**args, **validated}
        return self.run(args)


class ToolRegistry:
    """Registry of available tools keyed by name."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools or []:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if not tool.name:
            raise ValueError("Tool must have a non-empty name")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise KeyError(f"Unknown tool: {name}") from None

    def names(self) -> list[str]:
        return sorted(self._tools)

    def definitions(self) -> list[dict[str, Any]]:
        return [t.to_definition() for t in self._tools.values()]

    def execute(self, name: str, args: dict[str, Any]) -> ToolResult:
        return self.get(name).execute(args)
