"""OpenTelemetry tracing wrapper (optional).

Provides a thin abstraction over OpenTelemetry so the rest of the codebase
can emit spans without depending on the OTEL SDK directly.  If OTEL is
disabled or not installed, all operations become no-ops.

Usage::

    from app.observability.tracing import tracer

    with tracer.start_span("my_operation") as span:
        span.set_attribute("key", "value")
        ...
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from typing import Any


class NoOpSpan:
    """A span that does nothing (used when OTEL is disabled)."""

    def set_attribute(self, _key: str, _value: Any) -> None:
        pass

    def set_status(self, _status: str, _message: str = "") -> None:
        pass

    def record_exception(self, _exc: BaseException) -> None:
        pass

    def end(self) -> None:
        pass

    def __enter__(self) -> NoOpSpan:
        return self

    def __exit__(self, *_: Any) -> None:
        pass


class NoOpTracer:
    """Tracer that produces no-op spans."""

    @contextmanager
    def start_span(self, _name: str, **_kwargs: Any) -> Generator[NoOpSpan, None, None]:
        yield NoOpSpan()


class OTelTracer:
    """Real OpenTelemetry tracer (lazy-initialized)."""

    def __init__(self) -> None:
        self._tracer: Any = None

    def _ensure_init(self) -> None:
        if self._tracer is not None:
            return
        try:
            from opentelemetry import trace  # type: ignore[import-untyped]
            from opentelemetry.sdk.trace import TracerProvider  # type: ignore[import-untyped]
            from opentelemetry.sdk.trace.export import (  # type: ignore[import-untyped]
                BatchSpanProcessor,
                ConsoleSpanExporter,
            )

            provider = TracerProvider()
            # Always log to console for debugging; add OTLP exporter if endpoint provided.
            provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
            trace.set_tracer_provider(provider)
            self._tracer = trace.get_tracer("ci-triage-agent")
        except ImportError:
            # opentelemetry not installed -- fall back to no-op.
            self._tracer = NoOpTracer()

    @contextmanager
    def start_span(self, name: str, **kwargs: Any) -> Generator[NoOpSpan | Any, None, None]:
        self._ensure_init()
        try:
            with self._tracer.start_span(name, **kwargs) as span:  # type: ignore[union-attr]
                yield span
                return
        except Exception:  # noqa: BLE001
            pass
        # Fallback.
        yield NoOpSpan()


# Public API: use ``tracer.start_span(...)`` everywhere.
_tracer: OTelTracer | NoOpTracer | None = None


def get_tracer() -> OTelTracer | NoOpTracer:
    """Return the global tracer (initialised lazily)."""
    global _tracer  # noqa: PLW0603
    if _tracer is None:
        _tracer = OTelTracer()
    return _tracer


# Module-level convenience alias.
tracer = get_tracer()
