"""Support for BLOOMIN8 E-Ink Canvas buttons."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    CONF_MAC_ADDRESS,
    POST_WAKE_REFRESH_TIMEOUT_SECONDS,
    POST_WAKE_INITIAL_DELAY_SECONDS,
)
from .ble_wake import async_ble_wake

_LOGGER = logging.getLogger(__name__)


def _ble_wake_possible(hass: HomeAssistant) -> bool:
    """Return True if HA has a Bluetooth stack that can do connectable BLE.

    We only want to expose the Wake (Bluetooth) button if Home Assistant has
    at least one connectable BLE scanner (local adapter or BLE proxy).

    Notes:
    - Rely on HA Bluetooth integration state, not on whether the specific device
      is currently discovered in the cache (that can fluctuate).
    - Keep compatibility across HA versions by using feature detection.
    """

    # Newer HA versions expose async_scanner_count(connectable=...).
    try:
        scanner_count = bluetooth.async_scanner_count(hass, connectable=True)  # type: ignore[arg-type]
        return bool(scanner_count and scanner_count > 0)
    except TypeError:
        # Older signature without keyword.
        try:
            scanner_count = bluetooth.async_scanner_count(hass)  # type: ignore[call-arg]
            return bool(scanner_count and scanner_count > 0)
        except Exception:
            return False
    except Exception:
        return False

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

    # Optional BLE wake button
    # Only expose if:
    # - a Bluetooth MAC address is configured
    # - Home Assistant has Bluetooth/BLE proxy support available
    if mac_address and _ble_wake_possible(hass):
        buttons.append(EinkBluetoothWakeButton(hass, config_entry, host, name, mac_address))
    elif mac_address:
        _LOGGER.debug(
            "Not creating Wake (Bluetooth) button for %s: no connectable Bluetooth scanner available in Home Assistant",
            host,
        )

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
        result = await async_ble_wake(self.hass, self._mac, log_prefix="Wake (Bluetooth)")
        if result.ok:
            _LOGGER.info("BLE wake signal sent successfully")
        else:
            # Best-effort: still attempt a refresh below.
            _LOGGER.warning(
                "BLE wake failed for %s (%s)",
                self._mac,
                result.error or "unknown error",
            )
        
        # Always try to refresh device info after wake attempt.
        # The device might have been woken by user touch or another service,
        # so we try even if BLE wake failed.
        await self._refresh_device_info_after_wake()

    async def _refresh_device_info_after_wake(self) -> None:
        """Attempt to refresh device info after BLE wake with short timeout.
        
        Uses a shorter timeout than normal polling to quickly detect if device
        woke up without blocking too long if it didn't.
        """
        # Give the device time to bring up Wi‑Fi/HTTP after the BLE wake pulse.
        # Without this, the first HTTP attempt often fails with connection errors
        # or timeouts, which is expected but noisy in debug logs.
        await asyncio.sleep(POST_WAKE_INITIAL_DELAY_SECONDS)
        
        try:
            runtime_data = self._config_entry.runtime_data
            api_client = runtime_data.api_client
            coordinator = runtime_data.coordinator

            # Post-wake path: DO try the endpoint even if a quick ping fails.
            # Otherwise we can incorrectly skip while Wi‑Fi is still coming up.
            #
            # Note: wake=True here does *not* necessarily trigger another BLE wake.
            # If BLE auto-wake is disabled in the API client, it simply bypasses
            # the pre-ping skip and attempts the HTTP request.
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                device_info = await api_client.get_device_info(
                    wake=True,
                    timeout=POST_WAKE_REFRESH_TIMEOUT_SECONDS,
                )

                if device_info:
                    runtime_data.device_info = device_info
                    coordinator.async_set_updated_data(device_info)
                    _LOGGER.debug(
                        "Device info refreshed after BLE wake (attempt %s/%s)",
                        attempt,
                        max_attempts,
                    )
                    return

                if attempt < max_attempts:
                    await asyncio.sleep(2)

            _LOGGER.debug(
                "Device did not respond after BLE wake (still offline/asleep?)"
            )
        except Exception as err:
            _LOGGER.debug(
                "Failed to refresh device info after BLE wake: %s",
                err,
            )