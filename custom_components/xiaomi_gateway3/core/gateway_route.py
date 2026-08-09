"""Gateway selection for UI-managed multi-gateway sites."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def select_gateway(
    gateways: Iterable[Any],
    preferred_gateway: Any = None,
    default_gateway: Any = None,
    last_report_gateway: Any = None,
) -> Any | None:
    """Select the gateway used for device writes and reads.

    UI-managed sites set either an explicit auxiliary gateway or the main
    gateway. That route is strict: commands are not silently sent through a
    different physical gateway when the selected route is unavailable.

    Legacy gateway entries leave both route arguments unset and therefore
    preserve the upstream last-report, then first-available behavior.
    """
    gateways = tuple(gateways)
    available = tuple(i for i in gateways if getattr(i, "available", False))

    if preferred_gateway is not None:
        return preferred_gateway if preferred_gateway in available else None

    if default_gateway is not None:
        return default_gateway if default_gateway in available else None

    if last_report_gateway in available:
        return last_report_gateway

    return available[0] if available else None
