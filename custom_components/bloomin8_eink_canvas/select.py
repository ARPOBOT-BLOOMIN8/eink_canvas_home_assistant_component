"""Support for BLOOMIN8 E-Ink Canvas select inputs."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect

from .const import DOMAIN, DEFAULT_NAME, SIGNAL_DEVICE_INFO_UPDATED

_LOGGER = logging.getLogger(__name__)

# Sleep duration options mapping
SLEEP_DURATION_OPTIONS = {
    "30 minutes": 1800,
    "1 hour": 3600,
    "3 hours": 10800,
    "6 hours": 21600,
    "12 hours": 43200,
    "1 day": 86400,
    "2 days": 172800,
    "3 days": 259200,
    "5 days": 432000,
    "7 days": 604800,
}

# Max idle time options mapping
MAX_IDLE_OPTIONS = {
    "10 seconds": 10,
    "30 seconds": 30,
    "1 minute": 60,
    "2 minutes": 120,
    "3 minutes": 180,
    "5 minutes": 300,
    "10 minutes": 600,
    "never sleep": -1,
}

# Wake sensitivity options mapping
WAKE_SENSITIVITY_OPTIONS = {
    "very low": 1,
    "low": 2,
    "medium": 3,
    "high": 4,
    "very high": 5,
}

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas select inputs."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    selects = [
        EinkSleepDurationSelect(hass, config_entry, host, name),
        EinkMaxIdleSelect(hass, config_entry, host, name),
        EinkWakeSensitivitySelect(hass, config_entry, host, name),
    ]

    async_add_entities(selects, True)


class EinkBaseSelect(SelectEntity):
    """Base class for BLOOMIN8 E-Ink Canvas select inputs."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the select input."""
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = device_name
        self._attr_has_entity_name = True
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
        # Important: these entities compute their state in async_update() based on
        # runtime_data.device_info. If we don't force a refresh here, HA will only
        # re-write the *existing* state and the UI won't reflect external changes
        # (e.g. settings changed via curl + manual refresh).
        self.schedule_update_ha_state(True)

    @property
    def available(self) -> bool:
        """Return entity availability.

        Available if we have any cached device info. This allows select entities
        to show the last known value when the device is offline/asleep.
        """
        return self._get_device_info() is not None

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

    def _get_device_info(self) -> dict | None:
        """Get device info from shared runtime data."""
        runtime_data = self._config_entry.runtime_data
        return runtime_data.device_info


class EinkSleepDurationSelect(EinkBaseSelect):
    """Select input for sleep duration setting."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the select input."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Sleep Duration"
        self._attr_unique_id = f"eink_display_{host}_sleep_duration"
        self._attr_icon = "mdi:sleep"
        self._attr_options = list(SLEEP_DURATION_OPTIONS.keys())

    async def async_update(self) -> None:
        """Update the select input value."""
        device_info = self._get_device_info()
        if device_info:
            current_value = device_info.get("sleep_duration", 86400)
            # Find the matching option
            for option, value in SLEEP_DURATION_OPTIONS.items():
                if value == current_value:
                    self._attr_current_option = option
                    return
            # Default to 1 day if no match found
            self._attr_current_option = "1 day"
        else:
            self._attr_current_option = "1 day"

    async def async_select_option(self, option: str) -> None:
        """Set the sleep duration."""
        if option not in SLEEP_DURATION_OPTIONS:
            _LOGGER.error("Invalid sleep duration option: %s", option)
            return

        # Get current device settings
        device_info = self._get_device_info()
        if not device_info:
            _LOGGER.error("Cannot update sleep duration: device info not available")
            return

        # Call update_settings service with new sleep duration
        await self.hass.services.async_call(
            DOMAIN,
            "update_settings",
            {
                "name": device_info.get("name", "E-Ink Canvas"),
                "sleep_duration": SLEEP_DURATION_OPTIONS[option],
                "max_idle": device_info.get("max_idle", 300),
                "idx_wake_sens": device_info.get("idx_wake_sens", 3),
            },
            blocking=True,
        )


class EinkMaxIdleSelect(EinkBaseSelect):
    """Select input for max idle time setting."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the select input."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Max Idle Time"
        self._attr_unique_id = f"eink_display_{host}_max_idle"
        self._attr_icon = "mdi:timer"
        self._attr_options = list(MAX_IDLE_OPTIONS.keys())
        self._raw_max_idle: int | None = None

    @property
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Return entity specific state attributes."""
        if self._raw_max_idle is not None:
            return {"raw_max_idle_seconds": self._raw_max_idle}
        return None

    async def async_update(self) -> None:
        """Update the select input value."""
        device_info = self._get_device_info()
        if device_info:
            current_value = device_info.get("max_idle", 300)
            self._raw_max_idle = current_value
            # Find the matching option
            for option, value in MAX_IDLE_OPTIONS.items():
                if value == current_value:
                    self._attr_current_option = option
                    return
            # No exact match found - find the closest option
            # This handles firmware values not in our predefined list
            if current_value == -1:
                self._attr_current_option = "never sleep"
            elif current_value <= 10:
                self._attr_current_option = "10 seconds"
            elif current_value <= 30:
                self._attr_current_option = "30 seconds"
            elif current_value <= 60:
                self._attr_current_option = "1 minute"
            elif current_value <= 120:
                self._attr_current_option = "2 minutes"
            elif current_value <= 180:
                self._attr_current_option = "3 minutes"
            elif current_value <= 300:
                self._attr_current_option = "5 minutes"
            elif current_value <= 600:
                self._attr_current_option = "10 minutes"
            else:
                self._attr_current_option = "10 minutes"
            _LOGGER.debug(
                "max_idle=%s not in options, using closest match: %s",
                current_value,
                self._attr_current_option,
            )
        else:
            self._attr_current_option = "5 minutes"

    async def async_select_option(self, option: str) -> None:
        """Set the max idle time."""
        if option not in MAX_IDLE_OPTIONS:
            _LOGGER.error("Invalid max idle option: %s", option)
            return

        new_max_idle = MAX_IDLE_OPTIONS[option]
        # Very low idle times can make the device fall asleep between HA actions.
        # This is a common source of "device offline" confusion.
        if 0 < new_max_idle <= 30:
            _LOGGER.warning(
                "Max Idle Time set to %ss. Very low values can cause frequent deep sleep; "
                "consider enabling BLE auto-wake for reliable automations/UI.",
                new_max_idle,
            )

        # Get current device settings
        device_info = self._get_device_info()
        if not device_info:
            _LOGGER.error("Cannot update max idle time: device info not available")
            return

        # Call update_settings service with new max idle time
        await self.hass.services.async_call(
            DOMAIN,
            "update_settings",
            {
                "name": device_info.get("name", "E-Ink Canvas"),
                "sleep_duration": device_info.get("sleep_duration", 86400),
                "max_idle": new_max_idle,
                "idx_wake_sens": device_info.get("idx_wake_sens", 3),
            },
            blocking=True,
        )


class EinkWakeSensitivitySelect(EinkBaseSelect):
    """Select input for wake sensitivity setting."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the select input."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Wake Sensitivity"
        self._attr_unique_id = f"eink_display_{host}_wake_sensitivity"
        self._attr_icon = "mdi:gesture-tap"
        self._attr_options = list(WAKE_SENSITIVITY_OPTIONS.keys())

    async def async_update(self) -> None:
        """Update the select input value."""
        device_info = self._get_device_info()
        if device_info:
            current_value = device_info.get("idx_wake_sens", 3)
            # Find the matching option
            for option, value in WAKE_SENSITIVITY_OPTIONS.items():
                if value == current_value:
                    self._attr_current_option = option
                    return
            # Default to medium if no match found
            self._attr_current_option = "medium"
        else:
            self._attr_current_option = "medium"

    async def async_select_option(self, option: str) -> None:
        """Set the wake sensitivity."""
        if option not in WAKE_SENSITIVITY_OPTIONS:
            _LOGGER.error("Invalid wake sensitivity option: %s", option)
            return

        # Get current device settings
        device_info = self._get_device_info()
        if not device_info:
            _LOGGER.error("Cannot update wake sensitivity: device info not available")
            return

        # Call update_settings service with new wake sensitivity
        await self.hass.services.async_call(
            DOMAIN,
            "update_settings",
            {
                "name": device_info.get("name", "E-Ink Canvas"),
                "sleep_duration": device_info.get("sleep_duration", 86400),
                "max_idle": device_info.get("max_idle", 300),
                "idx_wake_sens": WAKE_SENSITIVITY_OPTIONS[option],
            },
            blocking=True,
        ) 