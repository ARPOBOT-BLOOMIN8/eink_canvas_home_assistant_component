"""Data update coordinator for BLOOMIN8 E-Ink Canvas.

We poll /deviceInfo (without waking the device) and distribute the resulting
snapshot to all entities.

For low-power/deep-sleep devices we support running with polling disabled:
- update_interval=None (no periodic polling)
- callers may push fresh snapshots via async_set_updated_data
"""

from __future__ import annotations

import logging
from datetime import datetime
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

    # Some firmwares/config paths appear to report unexpectedly low max_idle values.
    # For battery/sleep use-cases we prefer a conservative lower bound to avoid
    # keeping the device awake via periodic HTTP requests.
    if idle < DEFAULT_MAX_IDLE_SECONDS:
        idle = DEFAULT_MAX_IDLE_SECONDS

    # Keep a small margin to be strictly greater than max_idle.
    return int(idle + 30)


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
        self._last_offline_info_log: datetime | None = None

    async def async_refresh(self, *args: Any, **kwargs: Any) -> None:
        """Refresh data (quiet by default).

        Home Assistant's DataUpdateCoordinator may call `async_refresh` with
        keyword args like `log_failures`. We accept those for compatibility but
        force quiet logging because the Canvas is often asleep/offline.
        """
        # Force quiet refresh regardless of caller intent.
        kwargs["log_failures"] = False

        # Prefer the upstream implementation when possible.
        try:
            await super().async_refresh(*args, **kwargs)  # type: ignore[misc]
            return
        except TypeError:
            # Older HA versions with different signature.
            pass

        # Fallback to our handler.
        await self._handle_refresh(None)

    async def _handle_refresh(self, _now: Any) -> None:
        """Handle scheduled refreshes.

        The Canvas is expected to be offline/asleep much of the time.
        Scheduled polling should therefore *not* spam ERROR logs.
        """
        # Prefer the upstream implementation, but disable failure logging.
        # In Home Assistant this method typically delegates to an internal
        # refresh helper that accepts `log_failures`.
        # 1) Newer HA: async_refresh accepts log_failures
        try:
            await super().async_refresh(log_failures=False)  # type: ignore[misc]
            return
        except TypeError:
            pass

        # 2) Some HA versions have an internal _async_refresh helper
        parent_async_refresh = getattr(super(), "_async_refresh", None)
        if parent_async_refresh is not None:
            try:
                await parent_async_refresh(log_failures=False)
                return
            except TypeError:
                pass

        # Fallback path for older/unknown HA versions.
        # Keep behavior similar but avoid error spam for expected sleep/offline.
        try:
            data = await self._async_update_data()
        except UpdateFailed as err:
            self.last_update_success = False
            # Throttled info to avoid log spam.
            now = datetime.now()
            if (
                self._last_offline_info_log is None
                or (now - self._last_offline_info_log).total_seconds() >= 1800
            ):
                self._last_offline_info_log = now
                self.logger.info(
                    "Polling: device asleep/offline (expected): %s",
                    err,
                )
            else:
                self.logger.debug("Polling: device asleep/offline (expected): %s", err)
        except Exception:
            self.last_update_success = False
            self.logger.exception("Unexpected error fetching %s data", self.name)
        else:
            self.data = data
            self.last_update_success = True
        finally:
            self.async_update_listeners()

    async def _async_update_data(self) -> dict[str, Any] | None:
        """Fetch the latest device info snapshot.

        Important: this must NOT wake the device via BLE.
        """
        data = await self._api.get_device_info(wake=False)
        if data is None:
            # Treat absence of data as a failed update so entities become unavailable.
            # Note: scheduled polling suppresses ERROR logs in _handle_refresh.
            raise UpdateFailed("Device did not respond to /deviceInfo")

        # If polling is enabled, adapt the interval based on current device settings.
        # This is intentionally conservative so polling never keeps the device awake.
        if self._safe_polling and self.update_interval is not None:
            new_seconds = compute_safe_poll_interval_seconds(data.get("max_idle"))
            new_interval = timedelta(seconds=new_seconds)
            if new_interval != self.update_interval:
                self.logger.debug(
                    "Adjusting polling interval based on max_idle=%s: %ss -> %ss",
                    data.get("max_idle"),
                    int(self.update_interval.total_seconds()),
                    int(new_interval.total_seconds()),
                )
                self.update_interval = new_interval
        return data
