"""Support for BLOOMIN8 E-Ink Canvas buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas buttons."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    buttons = [
        EinkNextImageButton(hass, config_entry, host, name),
        EinkRebootButton(hass, config_entry, host, name),
        EinkClearScreenButton(hass, config_entry, host, name),
        EinkWhistleButton(hass, config_entry, host, name),
        EinkRefreshButton(hass, config_entry, host, name),
    ]

    async_add_entities(buttons, True)


class EinkBaseButton(ButtonEntity):
    """Base class for BLOOMIN8 E-Ink Canvas buttons."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = device_name
        self._attr_has_entity_name = True

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


class EinkNextImageButton(EinkBaseButton):
    """Button to show next image."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Next Image"
        self._attr_unique_id = f"eink_display_{host}_next_image"
        self._attr_icon = "mdi:skip-next"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "show_next",
            {},
            blocking=True,
        )



class EinkRebootButton(EinkBaseButton):
    """Button to reboot device."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Reboot"
        self._attr_unique_id = f"eink_display_{host}_reboot"
        self._attr_icon = "mdi:restart"
        self._attr_entity_category = EntityCategory.CONFIG

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "reboot",
            {},
            blocking=True,
        )


class EinkClearScreenButton(EinkBaseButton):
    """Button to clear screen."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Clear Screen"
        self._attr_unique_id = f"eink_display_{host}_clear_screen"
        self._attr_icon = "mdi:monitor-clean"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "clear_screen",
            {},
            blocking=True,
        )


class EinkWhistleButton(EinkBaseButton):
    """Button to send whistle (wake up)."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Whistle"
        self._attr_unique_id = f"eink_display_{host}_whistle"
        self._attr_icon = "mdi:whistle"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "whistle",
            {},
            blocking=True,
        )


class EinkRefreshButton(EinkBaseButton):
    """Button to refresh device info."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Refresh Info"
        self._attr_unique_id = f"eink_display_{host}_refresh"
        self._attr_icon = "mdi:refresh"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "refresh_device_info",
            {},
            blocking=True,
        )