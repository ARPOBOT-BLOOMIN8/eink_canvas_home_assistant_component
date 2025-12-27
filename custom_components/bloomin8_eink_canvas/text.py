"""Support for BLOOMIN8 E-Ink Canvas text inputs."""
from __future__ import annotations

import logging

from homeassistant.components.text import TextEntity, TextMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, DEFAULT_NAME, SIGNAL_DEVICE_INFO_UPDATED

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas text inputs."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    texts = [
        EinkDeviceNameText(hass, config_entry, host, name),
    ]

    async_add_entities(texts, True)


class EinkDeviceNameText(TextEntity):
    """Text input for device name setting."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the text input."""
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = device_name
        self._attr_has_entity_name = True
        self._attr_name = "Device Name"
        self._attr_unique_id = f"eink_display_{host}_device_name"
        self._attr_icon = "mdi:rename-box"
        self._attr_mode = TextMode.TEXT
        self._attr_native_min = 1
        self._attr_native_max = 50
        self._attr_entity_category = EntityCategory.CONFIG
        # Never poll directly; we update from shared coordinator/runtime cache.
        self._attr_should_poll = False
        self._unsub_dispatcher = None

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()

        signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{self._config_entry.entry_id}"
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            signal,
            self._handle_runtime_data_updated,
        )

    async def async_will_remove_from_hass(self) -> None:
        """Clean up callbacks."""
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_runtime_data_updated(self) -> None:
        """Handle runtime data updates (no network I/O)."""
        # Thread-safety: use sync helper safe from any thread.
        # Force a refresh so async_update() runs and pulls the latest value from
        # runtime_data.device_info.
        self.schedule_update_ha_state(True)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=self._device_name,
            manufacturer="BLOOMIN8",
            model="E-Ink Canvas",
            # configuration_url=f"http://{self._host}",  # Disabled to prevent external access
        )

    @property
    def available(self) -> bool:
        """Return entity availability.

        Available if we have any cached device info. This allows text entities
        to show the last known value when the device is offline/asleep.
        """
        return self._get_device_info() is not None

    def _get_device_info(self) -> dict | None:
        """Get device info from shared runtime data."""
        runtime_data = self._config_entry.runtime_data
        return runtime_data.device_info

    async def async_update(self) -> None:
        """Update the text input value."""
        device_info = self._get_device_info()
        if device_info:
            self._attr_native_value = device_info.get("name", "E-Ink Canvas")
        else:
            self._attr_native_value = "E-Ink Canvas"

    async def async_set_value(self, value: str) -> None:
        """Set the device name."""
        # Get current device settings
        device_info = self._get_device_info()
        if not device_info:
            _LOGGER.error("Cannot update device name: device info not available")
            return

        # Call update_settings service with new device name
        await self.hass.services.async_call(
            DOMAIN,
            "update_settings",
            {
                "name": value,
                "sleep_duration": device_info.get("sleep_duration", 86400),
                "max_idle": device_info.get("max_idle", 300),
                "idx_wake_sens": device_info.get("idx_wake_sens", 3),
            },
            blocking=True,
        ) 