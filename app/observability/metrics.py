"""Lightweight in-memory metrics collection.

Provides counters, gauges, and histograms that can be queried at runtime
or flushed to the persistence layer.  Thread-safe via simple locking.

Usage::

    from app.observability.metrics import metrics

    metrics.counter("llm_calls").inc()
    metrics.gauge("active_runs").set(3)
    metrics.histogram("tool_duration_ms").observe(120)
    snapshot = metrics.snapshot()
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Counter:
    """Monotonically increasing counter."""

    name: str
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def inc(self, value: float = 1.0) -> None:
        with self._lock:
            self._value += value

    @property
    def value(self) -> float:
        return self._value


@dataclass
class Gauge:
    """Value that can go up and down."""

    name: str
    _value: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, value: float = 1.0) -> None:
        with self._lock:
            self._value += value

    def dec(self, value: float = 1.0) -> None:
        with self._lock:
            self._value -= value

    @property
    def value(self) -> float:
        return self._value


@dataclass
class Histogram:
    """Distribution of observed values with pre-defined buckets."""

    name: str
    _buckets: dict[float, int] = field(default_factory=dict)
    _count: int = 0
    _sum: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # Default bucket boundaries (ms).
    BUCKETS: list[float] = field(
        default_factory=lambda: [10, 50, 100, 250, 500, 1000, 2500, 5000],
        repr=False,
    )

    def __post_init__(self) -> None:
        for b in self.BUCKETS:
            self._buckets.setdefault(b, 0)

    def observe(self, value: float) -> None:
        with self._lock:
            self._count += 1
            self._sum += value
            for bucket in self.BUCKETS:
                if value <= bucket:
                    self._buckets[bucket] = self._buckets.get(bucket, 0) + 1

    @property
    def count(self) -> int:
        return self._count

    @property
    def mean(self) -> float:
        return self._sum / self._count if self._count else 0.0

    def buckets(self) -> dict[float, int]:
        return dict(self._buckets)


class Metrics:
    """Registry of counters, gauges, and histograms."""

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str) -> Counter:
        if name not in self._counters:
            self._counters[name] = Counter(name=name)
        return self._counters[name]

    def gauge(self, name: str) -> Gauge:
        if name not in self._gauges:
            self._gauges[name] = Gauge(name=name)
        return self._gauges[name]

    def histogram(self, name: str) -> Histogram:
        if name not in self._histograms:
            self._histograms[name] = Histogram(name=name)
        return self._histograms[name]

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-serialisable snapshot of all metrics."""
        return {
            "counters": {n: c.value for n, c in self._counters.items()},
            "gauges": {n: g.value for n, g in self._gauges.items()},
            "histograms": {
                n: {"count": h.count, "mean": h.mean, "buckets": h.buckets()}
                for n, h in self._histograms.items()
            },
            "timestamp": time.time(),
        }

    def reset(self) -> None:
        """Clear all metrics (useful in tests)."""
        self._counters.clear()
        self._gauges.clear()
        self._histograms.clear()


# Global singleton.
metrics = Metrics()
