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

Data is persisted to disk so that after a Home Assistant restart, the last
known device state is immediately available (even if the device is asleep).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from .api_client import EinkCanvasApiClient
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 2
STORAGE_KEY_PREFIX = f"{DOMAIN}.coordinator_cache"


class EinkCanvasDeviceInfoCoordinator(DataUpdateCoordinator[dict[str, Any] | None]):
    """Coordinator that manages device info snapshots (no polling).

    Supports persistent caching: last known device data survives HA restarts.
    """

    def __init__(
        self,
        hass: HomeAssistant,
        *,
        api_client: EinkCanvasApiClient,
        entry_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="BLOOMIN8 E-Ink Canvas",
            update_interval=None,  # No polling — push-only
            always_update=False,  # Only notify entities when data actually changes
        )
        self._api = api_client
        self._entry_id = entry_id
        self._store: Store[dict[str, Any]] = Store(
            hass,
            STORAGE_VERSION,
            f"{STORAGE_KEY_PREFIX}.{entry_id}",
        )
        self._last_successful_update: datetime | None = None

    @property
    def last_successful_update(self) -> datetime | None:
        """Return the datetime of the last successful device info fetch."""
        return self._last_successful_update

    async def async_load_cached_data(self) -> None:
        """Load previously cached data from disk (call once during setup)."""
        cached = await self._store.async_load()
        if cached is not None:
            self.logger.debug("Restored cached device info from disk")
            # Extract and restore the timestamp if present.
            if "_last_update_iso" in cached:
                try:
                    self._last_successful_update = dt_util.parse_datetime(
                        cached.pop("_last_update_iso")
                    )
                except (ValueError, TypeError):
                    pass
            # Populate coordinator data without marking it as a *fresh* fetch.
            # Using self.async_set_updated_data() would overwrite the restored
            # timestamp with utcnow() and would immediately re-save the cache.
            super().async_set_updated_data(cached)

    async def _async_save_cache(self) -> None:
        """Persist the current data to disk (including timestamp)."""
        if self.data is not None:
            cache_data = dict(self.data)
            if self._last_successful_update is not None:
                cache_data["_last_update_iso"] = self._last_successful_update.isoformat()
            await self._store.async_save(cache_data)

    def async_set_updated_data(self, data: dict[str, Any] | None) -> None:
        """Update data and persist if non-None."""
        # Avoid redundant updates: some action flows may push the same snapshot
        # more than once within a short time window. Skipping identical pushes
        # prevents duplicate "Manually updated … data" coordinator logs and
        # unnecessary cache writes.
        if data is not None and self.data is not None and data == self.data:
            return

        if data is not None:
            # Update timestamp on fresh data.
            self._last_successful_update = dt_util.utcnow()
            # Treat pushed snapshots as successful updates.
            # DataUpdateCoordinator only toggles last_update_success when a refresh
            # runs; for our push-based model we must keep this in sync so the
            # diagnostic Device Info sensor can reflect reality after manual refresh/
            # BLE wake.
            self.last_update_success = True
            self.last_exception = None
        super().async_set_updated_data(data)
        if data is not None:
            # Fire-and-forget save (non-blocking).
            self.hass.async_create_task(self._async_save_cache())

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
