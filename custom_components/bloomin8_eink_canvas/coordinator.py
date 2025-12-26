"""Data update coordinator for BLOOMIN8 E-Ink Canvas.

This coordinator manages device info snapshots WITHOUT periodic polling.
The Canvas is a battery-powered device that sleeps for hours/days — any HTTP
request resets its idle timer and prevents sleep, causing severe battery drain.

Instead of polling, we use a push-based model:
- Services (upload, show_next, clear_screen, etc.) push fresh data after actions.
- The BLE Wake button triggers a refresh after waking the device.
- Entities show the last known value when the device is offline.

Callers may push fresh snapshots via async_set_updated_data() or trigger a
one-off refresh via async_request_refresh().
"""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .api_client import EinkCanvasApiClient

_LOGGER = logging.getLogger(__name__)


class EinkCanvasDeviceInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator that manages device info snapshots (no polling)."""

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_client: EinkCanvasApiClient,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="BLOOMIN8 E-Ink Canvas",
            update_interval=None,  # No polling — push-only
        )
        self._api = api_client

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch the latest device info snapshot.

        Important: this must NOT wake the device via BLE.

        Returns None if the device is offline/asleep. This is expected behavior
        for battery-powered devices. When called via async_request_refresh()
        (e.g., after a service call), failures are logged at DEBUG level only.
        """
        data = await self._api.get_device_info(wake=False)
        if data is None:
            # Device is offline/asleep - this is expected for battery devices.
            self.logger.debug("Device did not respond to /deviceInfo (asleep/offline)")
            return None
        return data
