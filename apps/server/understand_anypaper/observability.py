"""LLM observability wiring (OpenTelemetry -> Arize Phoenix or any OTLP backend).

agent-framework instruments agents and chat clients by default, but the spans go
nowhere until OTel providers with exporters are installed. Exporting uses batch
processors on background threads, so request latency is unaffected.
"""

import logging
import os

logger = logging.getLogger(__name__)

_OTLP_ENDPOINT_VARS = (
    "OTEL_EXPORTER_OTLP_ENDPOINT",
    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
)


def configure_observability() -> None:
    """Install OTel providers once at startup.

    No-op unless an OTLP endpoint is configured in the environment, so runs
    without a trace collector stay on the no-op tracer with zero overhead.
    Endpoint, protocol and sensitive-data capture are all read from standard
    env vars (OTEL_EXPORTER_OTLP_*, ENABLE_SENSITIVE_DATA) by agent-framework.
    """
    endpoint = next(
        (value for var in _OTLP_ENDPOINT_VARS if (value := os.getenv(var))), None
    )
    if not endpoint:
        return
    from agent_framework.observability import configure_otel_providers

    configure_otel_providers()
    logger.info("LLM tracing enabled, exporting OTLP spans to %s", endpoint)
