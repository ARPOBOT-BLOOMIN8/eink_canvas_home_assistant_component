"""Support for BLOOMIN8 E-Ink Canvas buttons."""
from __future__ import annotations

import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components.bluetooth import async_ble_device_from_address
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import DOMAIN, DEFAULT_NAME, CONF_MAC_ADDRESS, BLE_CHAR_UUID, BLE_WAKE_PAYLOAD

_LOGGER = logging.getLogger(__name__)

async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas buttons."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)
    mac_address = (config_entry.data.get(CONF_MAC_ADDRESS) or "").strip()

    buttons = [
        EinkNextImageButton(hass, config_entry, host, name),
        EinkSleepButton(hass, config_entry, host, name),
        EinkRebootButton(hass, config_entry, host, name),
        EinkClearScreenButton(hass, config_entry, host, name),
        EinkWhistleButton(hass, config_entry, host, name),
        EinkRefreshButton(hass, config_entry, host, name),
    ]

    # Optional BLE wake button (requires a configured Bluetooth MAC address)
    if mac_address:
        buttons.append(EinkBluetoothWakeButton(hass, config_entry, host, name, mac_address))

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


class EinkSleepButton(EinkBaseButton):
    """Button to put device to sleep."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Sleep"
        self._attr_unique_id = f"eink_display_{host}_sleep"
        self._attr_icon = "mdi:sleep"

    async def async_press(self) -> None:
        """Handle the button press."""
        await self.hass.services.async_call(
            DOMAIN,
            "sleep",
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


class EinkBluetoothWakeButton(EinkBaseButton):
    """Button to wake the device via Bluetooth Low Energy (BLE)."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        host: str,
        device_name: str,
        mac_address: str,
    ) -> None:
        """Initialize the button."""
        super().__init__(hass, config_entry, host, device_name)
        self._mac = mac_address
        self._attr_name = "Wake (Bluetooth)"
        self._attr_unique_id = f"eink_display_{host}_bt_wake"
        self._attr_icon = "mdi:bluetooth-connect"

    async def async_press(self) -> None:
        """Handle the button press."""
        # Resolve BLEDevice from HA Bluetooth integration cache
        device = async_ble_device_from_address(self.hass, self._mac, connectable=True)
        if not device:
            _LOGGER.warning(
                "Bluetooth device %s not found in HA Bluetooth cache or not connectable",
                self._mac,
            )
            return

        _LOGGER.info("Sending BLE wake signal to %s", self._mac)

        # Prefer HA-recommended connector for reliable connects and to avoid warnings
        # from habluetooth wrappers.
        try:
            from bleak_retry_connector import (  # type: ignore
                BleakClientWithServiceCache,
                establish_connection,
            )
        except ImportError:
            BleakClientWithServiceCache = None  # type: ignore[assignment]
            establish_connection = None  # type: ignore[assignment]

        # Import bleak lazily to avoid hard dependency during import/tests
        try:
            from bleak import BleakClient  # type: ignore
        except ImportError:
            _LOGGER.error(
                "Cannot send BLE wake signal because 'bleak' is not available in this environment"
            )
            return

        try:
            if establish_connection is not None and BleakClientWithServiceCache is not None:
                client = await establish_connection(
                    BleakClientWithServiceCache,
                    device,
                    name=getattr(device, "name", None) or self._mac,
                    max_attempts=4,
                )
                try:
                    await client.write_gatt_char(
                        BLE_CHAR_UUID,
                        BLE_WAKE_PAYLOAD,
                        response=True,
                    )
                    _LOGGER.info("BLE wake signal sent successfully")
                finally:
                    await client.disconnect()
            else:
                # Fallback for environments without bleak-retry-connector.
                async with BleakClient(device) as client:
                    if not client.is_connected:
                        _LOGGER.error("Failed to connect to %s", self._mac)
                        return

                    await client.write_gatt_char(
                        BLE_CHAR_UUID,
                        BLE_WAKE_PAYLOAD,
                        response=True,
                    )
                    _LOGGER.info("BLE wake signal sent successfully")
        except Exception as err:
            _LOGGER.error(
                "Failed to send BLE wake signal to %s (%s): %s",
                self._mac,
                type(err).__name__,
                err,
            )