"""Dispatcher helpers for runtime cache updates.

We keep this as a small functional helper (no mixins) to avoid MRO/super() pitfalls
with Home Assistant entity base classes.
"""

from __future__ import annotations

from collections.abc import Callable

from homeassistant.core import HomeAssistant
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import SIGNAL_DEVICE_INFO_UPDATED


def device_info_updated_signal(entry_id: str) -> str:
    """Return the dispatcher signal name for a config entry."""
    return f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry_id}"


def connect_device_info_updated(
    hass: HomeAssistant,
    *,
    entry_id: str,
    callback: Callable[[], None],
) -> Callable[[], None]:
    """Connect to the runtime 'device info updated' dispatcher signal."""
    return async_dispatcher_connect(hass, device_info_updated_signal(entry_id), callback)
