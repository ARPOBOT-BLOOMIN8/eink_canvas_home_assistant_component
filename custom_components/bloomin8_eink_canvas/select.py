"""Support for BLOOMIN8 E-Ink Canvas select inputs."""
from __future__ import annotations

import logging

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

# Sleep duration options mapping
SLEEP_DURATION_OPTIONS = {
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

    async def async_update(self) -> None:
        """Update the select input value."""
        device_info = self._get_device_info()
        if device_info:
            current_value = device_info.get("max_idle", 300)
            # Find the matching option
            for option, value in MAX_IDLE_OPTIONS.items():
                if value == current_value:
                    self._attr_current_option = option
                    return
            # Default to 5 minutes if no match found
            self._attr_current_option = "5 minutes"
        else:
            self._attr_current_option = "5 minutes"

    async def async_select_option(self, option: str) -> None:
        """Set the max idle time."""
        if option not in MAX_IDLE_OPTIONS:
            _LOGGER.error("Invalid max idle option: %s", option)
            return

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
                "max_idle": MAX_IDLE_OPTIONS[option],
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