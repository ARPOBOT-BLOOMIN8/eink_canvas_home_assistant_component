"""Data update coordinator for BLOOMIN8 E-Ink Canvas.

We poll /deviceInfo (without waking the device) and distribute the resulting
snapshot to all entities.

For low-power/deep-sleep devices we support running with polling disabled:
- update_interval=None (no periodic polling)
- callers may push fresh snapshots via async_set_updated_data
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api_client import EinkCanvasApiClient

_LOGGER = logging.getLogger(__name__)

# When polling is enabled and the device is configured to never sleep
# (max_idle = -1), we can poll more frequently.
DEFAULT_DEVICE_INFO_POLL_INTERVAL = timedelta(seconds=30)

# Fallback if we do not yet know max_idle.
DEFAULT_MAX_IDLE_SECONDS = 300


def compute_safe_poll_interval_seconds(max_idle: Any) -> int:
    """Compute a polling interval that should NOT keep the device awake.

    The device's `max_idle` is the inactivity window after which it may go to sleep.
    Any HTTP request can count as activity, so polling must be strictly larger than
    max_idle to avoid preventing sleep.

    Rules:
    - max_idle == -1 (never sleep): allow faster polling (30s).
    - max_idle > 0: use max_idle + 5s (minimal safety margin).
    - unknown/invalid: fall back to DEFAULT_MAX_IDLE_SECONDS + 5s.
    """
    try:
        idle = int(max_idle)
    except Exception:
        idle = DEFAULT_MAX_IDLE_SECONDS

    if idle == -1:
        return int(DEFAULT_DEVICE_INFO_POLL_INTERVAL.total_seconds())

    if idle <= 0:
        idle = DEFAULT_MAX_IDLE_SECONDS

    # Keep a small margin to be strictly greater than max_idle.
    return int(idle + 5)


class EinkCanvasDeviceInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator that fetches device info from the Canvas."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_client: EinkCanvasApiClient,
        update_interval: timedelta | None,
        safe_polling: bool = False,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="BLOOMIN8 E-Ink Canvas",
            update_interval=update_interval,
        )
        self._api = api_client
        self._safe_polling = bool(safe_polling)

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch the latest device info snapshot.

        Important: this must NOT wake the device via BLE.
        """
        data = await self._api.get_device_info(wake=False)
        if data is None:
            # Treat absence of data as a failed update so entities become unavailable.
            raise UpdateFailed("Device did not respond to /deviceInfo")

        # If polling is enabled, adapt the interval based on current device settings.
        # This is intentionally conservative so polling never keeps the device awake.
        if self._safe_polling and self.update_interval is not None:
            new_seconds = compute_safe_poll_interval_seconds(data.get("max_idle"))
            new_interval = timedelta(seconds=new_seconds)
            if new_interval != self.update_interval:
                self.update_interval = new_interval
        return data
