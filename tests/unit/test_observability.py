"""Tests for the observability subsystem."""

from __future__ import annotations

import json
import logging

from app.observability.logging import JSONFormatter, TextFormatter, get_logger, setup_logging
from app.observability.metrics import Counter, Gauge, Histogram, Metrics, metrics
from app.observability.tracing import NoOpSpan, NoOpTracer, get_tracer

# ------------------------------------------------------------------
# Logging
# ------------------------------------------------------------------

class TestJSONFormatter:
    def test_formats_record(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="hello %s", args=("world",), exc_info=None,
        )
        output = fmt.format(record)
        data = json.loads(output)
        assert data["level"] == "INFO"
        assert data["logger"] == "test"
        assert data["message"] == "hello world"
        assert "timestamp" in data

    def test_includes_exception(self):
        fmt = JSONFormatter()
        try:
            raise ValueError("boom")
        except ValueError:
            import sys
            exc_info = sys.exc_info()
            record = logging.LogRecord(
                name="test", level=logging.ERROR, pathname="", lineno=0,
                msg="failed", args=(), exc_info=exc_info,
            )
        output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]

    def test_includes_extra_fields(self):
        fmt = JSONFormatter()
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="", lineno=0,
            msg="step done", args=(), exc_info=None,
        )
        record.triage_id = "abc123"
        record.step = "diagnose"
        output = fmt.format(record)
        data = json.loads(output)
        assert data["triage_id"] == "abc123"
        assert data["step"] == "diagnose"


class TestTextFormatter:
    def test_formats_record(self):
        fmt = TextFormatter()
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="", lineno=0,
            msg="hello", args=(), exc_info=None,
        )
        output = fmt.format(record)
        assert "WARNING" in output
        assert "test: hello" in output


class TestSetupLogging:
    def test_setup_json(self):
        setup_logging(level="DEBUG", fmt="json")
        logger = get_logger("test.setup.json")
        # Should not raise; handler is configured.
        logger.info("json setup ok")

    def test_setup_text(self):
        setup_logging(level="WARNING", fmt="text")
        logger = get_logger("test.setup.text")
        # Should not raise; handler is configured.
        logger.warning("text setup ok")


# ------------------------------------------------------------------
# Metrics
# ------------------------------------------------------------------

class TestCounter:
    def test_inc(self):
        c = Counter(name="test_counter")
        c.inc()
        assert c.value == 1.0
        c.inc(5)
        assert c.value == 6.0


class TestGauge:
    def test_set_inc_dec(self):
        g = Gauge(name="test_gauge")
        g.set(10)
        assert g.value == 10.0
        g.inc(3)
        assert g.value == 13.0
        g.dec(5)
        assert g.value == 8.0


class TestHistogram:
    def test_observe(self):
        h = Histogram(name="test_hist")
        h.observe(25)
        h.observe(200)
        assert h.count == 2
        assert h.mean == 112.5

    def test_buckets(self):
        h = Histogram(name="test_hist")
        h.observe(5)
        buckets = h.buckets()
        # 5ms falls into the 10ms bucket.
        assert buckets[10] == 1


class TestMetrics:
    def test_snapshot(self):
        m = Metrics()
        m.counter("c").inc(3)
        m.gauge("g").set(7)
        m.histogram("h").observe(42)

        snap = m.snapshot()
        assert snap["counters"]["c"] == 3.0
        assert snap["gauges"]["g"] == 7.0
        assert snap["histograms"]["h"]["count"] == 1
        assert "timestamp" in snap

    def test_reset(self):
        m = Metrics()
        m.counter("c").inc()
        m.reset()
        snap = m.snapshot()
        assert snap["counters"] == {}

    def test_singleton_has_expected_counters(self):
        """The global metrics object is usable."""
        snap = metrics.snapshot()
        assert "counters" in snap


# ------------------------------------------------------------------
# Tracing (no-op fallback)
# ------------------------------------------------------------------

class TestNoOpTracer:
    def test_start_span_yields_noop(self):
        tracer = NoOpTracer()
        with tracer.start_span("test") as span:
            assert isinstance(span, NoOpSpan)
            span.set_attribute("k", "v")
            span.set_status("OK")

    def test_global_tracer_is_available(self):
        t = get_tracer()
        assert t is not None
        with t.start_span("noop") as s:
            assert isinstance(s, NoOpSpan)
