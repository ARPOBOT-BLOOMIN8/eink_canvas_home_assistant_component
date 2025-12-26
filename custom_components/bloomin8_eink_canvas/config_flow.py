"""Config flow for BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import asyncio
import logging
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api_client import EinkCanvasApiClient
from .const import (
    DOMAIN,
    CONF_NAME,
    CONF_MAC_ADDRESS,
    CONF_BLE_AUTO_WAKE,
    DEFAULT_BLE_AUTO_WAKE,
    CONF_ORIENTATION,
    CONF_FILL_MODE,
    CONF_CONTAIN_COLOR,
    ORIENTATION_PORTRAIT,
    ORIENTATION_LANDSCAPE,
    FILL_MODE_CONTAIN,
    FILL_MODE_COVER,
    FILL_MODE_AUTO,
    DEFAULT_ORIENTATION,
    DEFAULT_FILL_MODE,
    DEFAULT_CONTAIN_COLOR,
    CONTAIN_COLORS,
    ERROR_CANNOT_CONNECT,
    ERROR_INVALID_AUTH,
    ERROR_UNKNOWN,
    BLE_SERVICE_UUID,
    BLE_CHAR_UUID,
    BLE_WAKE_PAYLOAD,
)

_LOGGER = logging.getLogger(__name__)


CONF_BLE_DEVICE = "ble_device"


def _is_probably_bloomin8(service_info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """Best-effort check if a discovered BLE device looks like a Bloomin8 Canvas."""
    name = (service_info.name or "").lower()
    if "bloomin" in name or "canvas" in name:
        return True
    # Some devices advertise their primary service UUIDs.
    if BLE_SERVICE_UUID.lower() in {u.lower() for u in (service_info.service_uuids or [])}:
        return True
    return False


def _format_ble_label(service_info: bluetooth.BluetoothServiceInfoBleak) -> str:
    """Human-readable label for a BLE device selection."""
    if service_info.name:
        return f"{service_info.name} ({service_info.address})"
    return service_info.address


def _build_ble_selector_options(
    hass: HomeAssistant,
    *,
    connectable: bool,
) -> list[selector.SelectOptionDict]:
    """Build selector options from currently discovered BLE devices."""
    options: list[selector.SelectOptionDict] = []
    for info in bluetooth.async_discovered_service_info(hass, connectable):
        if not _is_probably_bloomin8(info):
            continue
        options.append(selector.SelectOptionDict(value=info.address, label=_format_ble_label(info)))

    # Stable ordering for UI
    options.sort(key=lambda o: o["label"])
    return options


async def _async_ble_wake_and_wait(hass: HomeAssistant, mac_address: str) -> None:
    """Try to wake the device via BLE, then wait for it to come online.

    This uses Home Assistant's Bluetooth integration cache (local adapters and/or
    ESPHome Bluetooth proxies) to resolve the BLEDevice.

    The wake step is best-effort: failures are logged and the config flow continues.
    """
    mac_address = (mac_address or "").strip()
    if not mac_address:
        return

    ble_device = bluetooth.async_ble_device_from_address(hass, mac_address, connectable=True)
    if not ble_device:
        _LOGGER.debug(
            "BLE wake skipped: device %s not found in HA Bluetooth cache or not connectable",
            mac_address,
        )
        return

    _LOGGER.info("Sending BLE wake signal to %s", mac_address)
    attempted = False
    try:
        attempted = True

        # Prefer HA-recommended connector when available.
        try:
            from bleak_retry_connector import (  # type: ignore
                BleakClientWithServiceCache,
                establish_connection,
            )

            client = await establish_connection(
                BleakClientWithServiceCache,
                ble_device,
                name=getattr(ble_device, "name", None) or mac_address,
                max_attempts=4,
            )
            try:
                await client.write_gatt_char(BLE_CHAR_UUID, BLE_WAKE_PAYLOAD, response=True)
                _LOGGER.info("BLE wake signal sent to %s", mac_address)
            finally:
                await client.disconnect()
        except ImportError:
            # Import bleak lazily to avoid hard dependency during import/tests
            try:
                from bleak import BleakClient  # type: ignore
            except ImportError:
                _LOGGER.warning(
                    "BLE wake skipped: 'bleak' is not available in this environment"
                )
                return

            async with BleakClient(ble_device) as client:
                if not client.is_connected:
                    _LOGGER.warning("BLE wake: failed to connect to %s", mac_address)
                else:
                    await client.write_gatt_char(BLE_CHAR_UUID, BLE_WAKE_PAYLOAD, response=True)
                    _LOGGER.info("BLE wake signal sent to %s", mac_address)
    except Exception as err:
        _LOGGER.warning(
            "BLE wake failed for %s (%s): %s",
            mac_address,
            type(err).__name__,
            err,
        )
    finally:
        # Per requirement: if BLE is set up, give the device time to boot Wi-Fi.
        # Only wait if we at least attempted a BLE connection (device was found).
        if attempted:
            await asyncio.sleep(10)


async def validate_input(hass: HomeAssistant, data: dict) -> dict:
    """Validate the user input allows us to connect."""
    host = data[CONF_HOST]
    _LOGGER.info("Attempting to connect to device at: %s", host)

    api_client = EinkCanvasApiClient(hass, host)

    # Try to get device info to verify connection
    # Config flow should not wake the device via BLE (no background side effects).
    device_info = await api_client.get_device_info(wake=False)
    if device_info is None:
        _LOGGER.error("Failed to connect to device at %s - no response from /deviceInfo endpoint", host)
        raise CannotConnect

    _LOGGER.info("Successfully connected to device at %s", host)
    return {"title": data[CONF_NAME]}

class EinkDisplayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLOOMIN8 E-Ink Canvas."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._prefill_mac: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery."""
        if not _is_probably_bloomin8(discovery_info):
            return self.async_abort(reason="not_supported")

        await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # We still need the IP-based setup fields, but we can prefill the MAC.
        self._prefill_mac = discovery_info.address
        return await self.async_step_user()

    async def async_step_reconfigure(self, user_input=None) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            try:
                # Map dropdown selection -> mac_address if provided.
                selected = (user_input.get(CONF_BLE_DEVICE) or "").strip()
                if selected and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    user_input[CONF_MAC_ADDRESS] = selected
                user_input.pop(CONF_BLE_DEVICE, None)

                # If BLE is configured, wake first and wait for Wi-Fi to come up.
                await _async_ble_wake_and_wait(self.hass, user_input.get(CONF_MAC_ADDRESS, ""))

                await validate_input(self.hass, user_input)
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=user_input,
                )
            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:
                _LOGGER.exception("Unexpected error during reconfigure: %s", err)
                errors["base"] = ERROR_UNKNOWN

        connectable_options = _build_ble_selector_options(self.hass, connectable=True)
        any_options = _build_ble_selector_options(self.hass, connectable=False)

        schema_dict: dict = {
            vol.Required(CONF_HOST, default=reconfigure_entry.data.get(CONF_HOST, "")): str,
            vol.Required(CONF_NAME, default=reconfigure_entry.data.get(CONF_NAME, "BLOOMIN8 E-Ink Canvas")): str,
        }

        # Prefer connectable devices for wake (GATT write requires a connection).
        if connectable_options:
            schema_dict[vol.Optional(CONF_BLE_DEVICE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=connectable_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        elif any_options:
            schema_dict[vol.Optional(CONF_BLE_DEVICE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=any_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        schema_dict.update(
            {
                vol.Optional(CONF_MAC_ADDRESS, default=reconfigure_entry.data.get(CONF_MAC_ADDRESS, "")): str,
                vol.Optional(
                    CONF_BLE_AUTO_WAKE,
                    default=reconfigure_entry.data.get(CONF_BLE_AUTO_WAKE, DEFAULT_BLE_AUTO_WAKE),
                ): bool,
                vol.Required(
                    CONF_ORIENTATION,
                    default=reconfigure_entry.data.get(CONF_ORIENTATION, DEFAULT_ORIENTATION),
                ): vol.In([ORIENTATION_PORTRAIT, ORIENTATION_LANDSCAPE]),
                vol.Required(
                    CONF_FILL_MODE,
                    default=reconfigure_entry.data.get(CONF_FILL_MODE, DEFAULT_FILL_MODE),
                ): vol.In([FILL_MODE_AUTO, FILL_MODE_CONTAIN, FILL_MODE_COVER]),
                vol.Required(
                    CONF_CONTAIN_COLOR,
                    default=reconfigure_entry.data.get(CONF_CONTAIN_COLOR, DEFAULT_CONTAIN_COLOR),
                ): vol.In(list(CONTAIN_COLORS.keys())),
            }
        )

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Handle the initial step."""
        errors = {}

        if user_input is not None:
            try:
                # Map dropdown selection -> mac_address if provided.
                selected = (user_input.get(CONF_BLE_DEVICE) or "").strip()
                if selected and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    user_input[CONF_MAC_ADDRESS] = selected
                user_input.pop(CONF_BLE_DEVICE, None)

                # If BLE is configured, wake first and wait for Wi-Fi to come up.
                await _async_ble_wake_and_wait(self.hass, user_input.get(CONF_MAC_ADDRESS, ""))

                info = await validate_input(self.hass, user_input)
                return self.async_create_entry(
                    title=info["title"],
                    data=user_input
                )
            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during config flow: %s", err)
                errors["base"] = ERROR_UNKNOWN

        connectable_options = _build_ble_selector_options(self.hass, connectable=True)
        any_options = _build_ble_selector_options(self.hass, connectable=False)

        schema_dict: dict = {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_NAME, default="BLOOMIN8 E-Ink Canvas"): str,
        }

        # Prefer connectable devices for wake (GATT write requires a connection).
        if connectable_options:
            schema_dict[vol.Optional(CONF_BLE_DEVICE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=connectable_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )
        elif any_options:
            schema_dict[vol.Optional(CONF_BLE_DEVICE)] = selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=any_options,
                    mode=selector.SelectSelectorMode.DROPDOWN,
                )
            )

        schema_dict.update(
            {
                vol.Optional(CONF_MAC_ADDRESS, default=(self._prefill_mac or "")): str,
                vol.Optional(CONF_BLE_AUTO_WAKE, default=DEFAULT_BLE_AUTO_WAKE): bool,
                vol.Required(
                    CONF_ORIENTATION,
                    default=DEFAULT_ORIENTATION,
                ): vol.In([ORIENTATION_PORTRAIT, ORIENTATION_LANDSCAPE]),
                vol.Required(
                    CONF_FILL_MODE,
                    default=DEFAULT_FILL_MODE,
                ): vol.In([FILL_MODE_AUTO, FILL_MODE_CONTAIN, FILL_MODE_COVER]),
                vol.Required(
                    CONF_CONTAIN_COLOR,
                    default=DEFAULT_CONTAIN_COLOR,
                ): vol.In(list(CONTAIN_COLORS.keys())),
            }
        )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
