"""OpenTelemetry tracing — optional, lazy-imported.

We don't force an OTEL dependency in pyproject because many customers have
their own tracing stack. If `opentelemetry` is available, `setup_tracing`
wires the FastAPI app; otherwise `trace_span` is a no-op contextmanager.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Optional


try:
    from opentelemetry import trace as _otel_trace  # type: ignore
    _HAS_OTEL = True
except Exception:
    _HAS_OTEL = False


def setup_tracing(
    service_name: str,
    exporter_endpoint: Optional[str] = None,
) -> bool:
    """Attempt to set up OTEL tracing. Returns True on success, False otherwise."""
    if not _HAS_OTEL:
        return False
    try:
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore

        provider = TracerProvider(
            resource=Resource.create({"service.name": service_name})
        )
        if exporter_endpoint:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (  # type: ignore
                OTLPSpanExporter,
            )
            exporter = OTLPSpanExporter(endpoint=exporter_endpoint)
            provider.add_span_processor(BatchSpanProcessor(exporter))

        _otel_trace.set_tracer_provider(provider)
        return True
    except Exception:
        return False


@contextmanager
def trace_span(name: str, **attrs: Any):
    """Context manager that produces an OTEL span if OTEL is available."""
    if not _HAS_OTEL:
        yield None
        return
    tracer = _otel_trace.get_tracer("trustlens")
    with tracer.start_as_current_span(name) as span:
        for k, v in attrs.items():
            try:
                span.set_attribute(k, v)
            except Exception:
                pass
        yield span
