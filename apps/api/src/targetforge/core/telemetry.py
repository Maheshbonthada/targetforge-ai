"""Application Insights via the OpenTelemetry distro. Same pattern as
TrialReady."""

from __future__ import annotations

import structlog

logger = structlog.get_logger(__name__)


def configure_telemetry(connection_string: str) -> None:
    if not connection_string:
        logger.warning("telemetry.disabled", reason="no connection string configured (expected in local dev)")
        return

    from azure.monitor.opentelemetry import configure_azure_monitor

    configure_azure_monitor(connection_string=connection_string)
    logger.info("telemetry.configured")
