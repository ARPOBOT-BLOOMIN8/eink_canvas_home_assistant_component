"""Config flow for BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import selector

from .api_client import EinkCanvasApiClient
from .ble_wake import async_ble_wake
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
    BLE_MANUFACTURER_ID,
    BLE_SERVICE_UUIDS,
)

_LOGGER = logging.getLogger(__name__)


CONF_BLE_DEVICE = "ble_device"
CONF_DISCOVERED_DEVICE = "discovered_device"


_EXPECTED_MS_CODE = "ARPO"
_EXPECTED_TYPE = "warptrek"


@dataclass(slots=True)
class _DiscoveredCandidate:
    host: str
    name: str | None
    sn: str
    bt_mac_norm: str
    ms_code: str | None = None
    dev_type: str | None = None


def _normalize_bt_mac(bt_mac: str | None) -> str | None:
    """Normalize deviceInfo.bt_mac (e.g. F49042163F47) to AA:BB:CC:DD:EE:FF."""
    if not bt_mac:
        return None
    s = str(bt_mac).strip().lower()
    s = re.sub(r"[^0-9a-f]", "", s)
    if len(s) != 12:
        return None
    return ":".join([s[i : i + 2] for i in range(0, 12, 2)])


def _deviceinfo_signature_matches(device_info: dict[str, Any] | None) -> bool:
    if not device_info:
        return False

    sn = (device_info.get("sn") or "").strip()
    bt_mac = (device_info.get("bt_mac") or "").strip()
    if not sn or not bt_mac:
        return False

    ms_code = (device_info.get("ms_code") or "").strip()
    dev_type = (device_info.get("type") or "").strip()
    return (ms_code == _EXPECTED_MS_CODE) or (dev_type == _EXPECTED_TYPE)


async def _async_mdns_http_candidates(
    hass: HomeAssistant,
    *,
    browse_timeout: float = 5.0,
    max_instances: int = 40,
    max_resolve: int = 25,
) -> list[str]:
    """Browse _http._tcp.local. via Zeroconf and return candidate IPv4 addresses.

    We keep this conservative:
    - _http._tcp is very generic, so we cap instance count and resolution.
    - Return unique IPv4 strings (e.g. "192.168.1.10").
    """

    try:
        from homeassistant.components import zeroconf as ha_zeroconf
        from zeroconf import ServiceStateChange
        from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo
    except Exception as err:  # pragma: no cover
        _LOGGER.debug("Zeroconf libraries not available (%s: %s)", type(err).__name__, err)
        return []

    # Home Assistant 2025.12+: use the shared async instance.
    # Older versions had a different wrapper shape.
    try:
        zc = await ha_zeroconf.async_get_async_instance(hass)
    except AttributeError:
        # Fallback for older Home Assistant builds.
        zc = await ha_zeroconf.async_get_instance(hass)
    except Exception as err:
        _LOGGER.debug("Failed to get Zeroconf instance (%s: %s)", type(err).__name__, err)
        return []

    # zeroconf asyncio helpers typically want an *AsyncZeroconf* instance.
    # Depending on HA version, we may have:
    # - HaAsyncZeroconf (wrapper) with .zeroconf (AsyncZeroconf)
    # - HaAsyncZeroconf (wrapper) with .aiozc (AsyncZeroconf)
    # - AsyncZeroconf itself
    async_zeroconf = getattr(zc, "zeroconf", None) or getattr(zc, "aiozc", None) or zc

    service_type = "_http._tcp.local."
    seen_names: list[str] = []

    # NOTE: Newer zeroconf versions call handlers with keyword arguments
    # (e.g. zeroconf=..., service_type=..., name=..., state_change=...).
    # Older versions pass positional args.
    def _on_state_change(
        zeroconf=None,  # noqa: ANN001 - external callback signature
        service_type=None,  # noqa: ANN001 - external callback signature
        name: str | None = None,
        state_change: ServiceStateChange | None = None,
        **_kwargs: Any,
    ) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        if name and name not in seen_names:
            seen_names.append(name)

    browser = AsyncServiceBrowser(async_zeroconf, service_type, handlers=[_on_state_change])
    try:
        await asyncio.sleep(max(0.5, float(browse_timeout)))
    finally:
        try:
            await browser.async_cancel()
        except Exception:
            pass

    names = seen_names[: max(0, int(max_instances))]
    ips: set[str] = set()

    # Resolve a capped number of instances to avoid overloading the network.
    for name in names[: max(0, int(max_resolve))]:
        try:
            info = AsyncServiceInfo(service_type, name)
            ok = await info.async_request(async_zeroconf, timeout=2000)
            if not ok:
                continue
            for addr in info.parsed_addresses():
                # Keep IPv4 only for now.
                if ":" in addr:
                    continue
                ips.add(addr)
        except Exception:
            continue

    return sorted(ips)


def _ble_wake_possible(hass: HomeAssistant) -> bool:
    """Return True if HA has a Bluetooth stack that can do connectable BLE.

    We hide BLE-related config fields when Home Assistant has no Bluetooth
    integration/scanner (no local adapter and no BLE proxies).
    """

    try:
        count = bluetooth.async_scanner_count(hass, connectable=True)  # type: ignore[arg-type]
        return bool(count and count > 0)
    except TypeError:
        # Older HA versions may not support the connectable kwarg.
        try:
            count = bluetooth.async_scanner_count(hass)  # type: ignore[call-arg]
            return bool(count and count > 0)
        except Exception:
            return False
    except Exception:
        return False


def _is_probably_bloomin8(service_info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    """Best-effort check if a discovered BLE device looks like a Bloomin8 Canvas."""
    # Prefer stable identifiers from the advertisement payload.
    manufacturer_data = getattr(service_info, "manufacturer_data", None) or {}
    if BLE_MANUFACTURER_ID in manufacturer_data:
        return True

    uuids = {u.lower() for u in (service_info.service_uuids or [])}
    if any(candidate.lower() in uuids for candidate in BLE_SERVICE_UUIDS):
        return True

    # Very conservative fallback: some devices may not advertise service UUIDs
    # reliably, but typically include a brand prefix in the name.
    name = (service_info.name or "").strip().lower()
    return name.startswith("bloomin8")


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
    """Try to wake the device via BLE, then wait for Wi-Fi/HTTP to come up.

    Best-effort: failures are logged and the config flow continues.
    """
    mac_address = (mac_address or "").strip()
    if not mac_address:
        return

    result = await async_ble_wake(hass, mac_address, log_prefix="ConfigFlow BLE wake")
    if result.attempted:
        # Give the device time to bring up Wi‑Fi/HTTP after the BLE wake pulse.
        _LOGGER.debug("ConfigFlow BLE wake attempted; waiting ~10s for Wi-Fi/HTTP")
        await asyncio.sleep(10)
        _LOGGER.debug("ConfigFlow BLE wake wait finished")


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

    sn = (device_info.get("sn") or "").strip() if isinstance(device_info, dict) else ""
    bt_mac_norm = _normalize_bt_mac(device_info.get("bt_mac")) if isinstance(device_info, dict) else None

    return {
        "title": data[CONF_NAME],
        "device_info": device_info,
        "sn": sn or None,
        "bt_mac_norm": bt_mac_norm,
    }

class EinkDisplayConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for BLOOMIN8 E-Ink Canvas."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._prefill_mac: str | None = None
        self._discovered: dict[str, _DiscoveredCandidate] = {}
        self._discovered_selected: str | None = None

    async def async_step_bluetooth(
        self, discovery_info: bluetooth.BluetoothServiceInfoBleak
    ) -> ConfigFlowResult:
        """Handle Bluetooth discovery."""
        if not _is_probably_bloomin8(discovery_info):
            return self.async_abort(reason="not_supported")

        # If we already have an entry that uses this BLE address for wake, abort.
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if (entry.data.get(CONF_MAC_ADDRESS) or "").strip() == discovery_info.address:
                return self.async_abort(reason="already_configured")

        # Avoid spawning multiple discovery flows for repeated advertisements.
        try:
            await self.async_set_unique_id(discovery_info.address, raise_on_progress=True)
        except TypeError:
            # Older Home Assistant versions may not support raise_on_progress.
            await self.async_set_unique_id(discovery_info.address)
        self._abort_if_unique_id_configured()

        # Used by Home Assistant to render the "Discovered" card label.
        self.context["title_placeholders"] = {
            "name": (discovery_info.name or "BLOOMIN8 E-Ink Canvas").strip(),
            "address": discovery_info.address,
        }

        # We still need the IP-based setup fields, but we can prefill the MAC.
        self._prefill_mac = discovery_info.address
        # IMPORTANT: When the flow is started via the "Discovered" Bluetooth card,
        # jumping directly into the manual step looks identical to the old flow.
        # Show the menu so users can choose mDNS discovery as well.
        return await self.async_step_user()

    async def async_step_user(self, user_input=None) -> ConfigFlowResult:
        """Entry point: choose manual setup or network discovery."""
        return self.async_show_menu(
            step_id="user",
            menu_options=["manual", "discover"],
        )

    async def async_step_discover(self, user_input=None) -> ConfigFlowResult:
        """Discover candidate devices via mDNS (_http._tcp.local.) and verify via /deviceInfo."""
        errors: dict[str, str] = {}

        if user_input is not None:
            selected = (user_input.get(CONF_DISCOVERED_DEVICE) or "").strip()
            if selected and selected in self._discovered:
                self._discovered_selected = selected
                return await self.async_step_discover_confirm()
            # If the form was empty (no devices found), just retry.

        # Scan the LAN once per entry into this step.
        self._discovered = {}
        self._discovered_selected = None

        # If the flow was started via Bluetooth discovery, we know the target BLE MAC.
        # Use it to disambiguate multiple devices on the network.
        target_bt_mac_norm = _normalize_bt_mac(self._prefill_mac)

        # Optional: if the flow was started via Bluetooth discovery we already know
        # a BLE address. Wake first so the device brings up Wi‑Fi/mDNS/HTTP.
        if self._prefill_mac and _ble_wake_possible(self.hass):
            _LOGGER.debug(
                "Discovery pre-wake via BLE before mDNS scan (mac=%s)",
                self._prefill_mac,
            )
            await _async_ble_wake_and_wait(self.hass, self._prefill_mac)

        ips = await _async_mdns_http_candidates(self.hass, browse_timeout=5.0)
        _LOGGER.debug("mDNS browse returned %d candidate IPv4 address(es)", len(ips))
        if not ips:
            errors["base"] = ERROR_CANNOT_CONNECT
            return self.async_show_form(
                step_id="discover",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        sem = asyncio.Semaphore(5)

        async def _probe(ip: str) -> None:
            async with sem:
                try:
                    api_client = EinkCanvasApiClient(self.hass, ip)
                    # No BLE MAC is configured for these ephemeral probe clients.
                    # If we needed to wake, we already did a best-effort BLE pre-wake above.
                    device_info = await api_client.get_device_info(
                        wake=False,
                        timeout=3,
                        # mDNS/_http._tcp discovery is generic; most candidates are NOT Bloomin8.
                        # Expected 404/HTML responses must not spam ERROR logs.
                        log_errors=False,
                    )
                except Exception:
                    return
                if not _deviceinfo_signature_matches(device_info):
                    return
                sn = (device_info.get("sn") or "").strip()
                bt_mac_norm = _normalize_bt_mac(device_info.get("bt_mac"))
                if not sn or not bt_mac_norm:
                    return

                # If we have a target BLE MAC (from Bluetooth discovery), only accept
                # the IP whose /deviceInfo.bt_mac matches it.
                if target_bt_mac_norm and bt_mac_norm != target_bt_mac_norm:
                    _LOGGER.debug(
                        "Discarding mDNS candidate %s: bt_mac mismatch (device=%s, target=%s)",
                        ip,
                        bt_mac_norm,
                        target_bt_mac_norm,
                    )
                    return
                self._discovered[ip] = _DiscoveredCandidate(
                    host=ip,
                    name=(device_info.get("name") or None),
                    sn=sn,
                    bt_mac_norm=bt_mac_norm,
                    ms_code=(device_info.get("ms_code") or None),
                    dev_type=(device_info.get("type") or None),
                )

        await asyncio.gather(*[_probe(ip) for ip in ips])

        _LOGGER.debug(
            "mDNS discovery verified %d Bloomin8 candidate(s) via GET /deviceInfo",
            len(self._discovered),
        )

        if not self._discovered:
            errors["base"] = ERROR_CANNOT_CONNECT
            return self.async_show_form(
                step_id="discover",
                data_schema=vol.Schema({}),
                errors=errors,
            )

        options: list[selector.SelectOptionDict] = []
        for ip, cand in sorted(self._discovered.items(), key=lambda kv: kv[0]):
            # Keep UI compact: show only the friendly name and the IP address.
            label = f"{cand.name or 'BLOOMIN8 Canvas'} | {ip}"
            options.append(selector.SelectOptionDict(value=ip, label=label))

        schema = vol.Schema(
            {
                vol.Required(CONF_DISCOVERED_DEVICE): selector.SelectSelector(
                    selector.SelectSelectorConfig(
                        options=options,
                        mode=selector.SelectSelectorMode.DROPDOWN,
                    )
                )
            }
        )

        return self.async_show_form(step_id="discover", data_schema=schema, errors=errors)

    async def async_step_discover_confirm(self, user_input=None) -> ConfigFlowResult:
        """Confirm setup for a discovered device (prefilled host/name/mac)."""
        errors: dict[str, str] = {}

        cand = self._discovered.get(self._discovered_selected or "")
        if cand is None:
            return await self.async_step_discover()

        if user_input is not None:
            try:
                selected = (user_input.get(CONF_BLE_DEVICE) or "").strip()
                if selected and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    user_input[CONF_MAC_ADDRESS] = selected
                user_input.pop(CONF_BLE_DEVICE, None)

                # Best-effort wake before validation/entry creation:
                # - Prefer an explicit MAC (user selection / input)
                # - Otherwise use the MAC we derived from /deviceInfo during discovery
                wake_mac = (user_input.get(CONF_MAC_ADDRESS) or "").strip()
                if not wake_mac and _ble_wake_possible(self.hass):
                    wake_mac = (cand.bt_mac_norm or "").strip()
                await _async_ble_wake_and_wait(self.hass, wake_mac)

                info = await validate_input(self.hass, user_input)

                # Prefer SN as stable identity.
                sn = info.get("sn")
                if sn:
                    await self.async_set_unique_id(sn)
                    self._abort_if_unique_id_configured()

                # If Bluetooth is available and we learned bt_mac, store it for future BLE wakes.
                if _ble_wake_possible(self.hass) and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    bt_mac_norm = info.get("bt_mac_norm")
                    if bt_mac_norm:
                        user_input[CONF_MAC_ADDRESS] = bt_mac_norm

                return self.async_create_entry(title=info["title"], data=user_input)
            except CannotConnect:
                errors["base"] = ERROR_CANNOT_CONNECT
            except InvalidAuth:
                errors["base"] = ERROR_INVALID_AUTH
            except Exception as err:  # pylint: disable=broad-except
                _LOGGER.exception("Unexpected error during config flow (discover_confirm): %s", err)
                errors["base"] = ERROR_UNKNOWN

        # Prefill schema with discovered values.
        ble_possible = _ble_wake_possible(self.hass)
        coming_from_ble_discovery = bool((self._prefill_mac or "").strip())
        connectable_options = (
            _build_ble_selector_options(self.hass, connectable=True)
            if ble_possible and not coming_from_ble_discovery
            else []
        )
        any_options = (
            _build_ble_selector_options(self.hass, connectable=False)
            if ble_possible and not coming_from_ble_discovery
            else []
        )

        schema_dict: dict = {
            vol.Required(CONF_HOST, default=cand.host): str,
            vol.Required(CONF_NAME, default=(cand.name or "BLOOMIN8 E-Ink Canvas")): str,
        }

        if ble_possible:
            # If the flow was started via Bluetooth discovery, we already know the
            # correct BLE MAC. A separate dropdown is confusing, so we hide it.
            if not coming_from_ble_discovery:
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
                    vol.Optional(
                        CONF_MAC_ADDRESS,
                        default=(self._prefill_mac or cand.bt_mac_norm),
                    ): str,
                    vol.Optional(CONF_BLE_AUTO_WAKE, default=DEFAULT_BLE_AUTO_WAKE): bool,
                }
            )

        schema_dict.update(
            {
                vol.Required(CONF_ORIENTATION, default=DEFAULT_ORIENTATION): vol.In(
                    [ORIENTATION_PORTRAIT, ORIENTATION_LANDSCAPE]
                ),
                vol.Required(CONF_FILL_MODE, default=DEFAULT_FILL_MODE): vol.In(
                    [FILL_MODE_AUTO, FILL_MODE_CONTAIN, FILL_MODE_COVER]
                ),
                vol.Required(CONF_CONTAIN_COLOR, default=DEFAULT_CONTAIN_COLOR): vol.In(
                    list(CONTAIN_COLORS.keys())
                ),
            }
        )

        return self.async_show_form(
            step_id="discover_confirm",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

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

                info = await validate_input(self.hass, user_input)

                # If Bluetooth is available and we learned bt_mac, store it for future BLE wakes.
                if _ble_wake_possible(self.hass) and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    bt_mac_norm = info.get("bt_mac_norm")
                    if bt_mac_norm:
                        user_input[CONF_MAC_ADDRESS] = bt_mac_norm

                # If the entry has no unique_id yet, set it to SN (stable identity).
                # (Existing entries keep their unique_id; we only fill missing ones.)
                sn = info.get("sn")
                if sn and not reconfigure_entry.unique_id:
                    await self.async_set_unique_id(sn)

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

        ble_possible = _ble_wake_possible(self.hass)
        connectable_options = _build_ble_selector_options(self.hass, connectable=True) if ble_possible else []
        any_options = _build_ble_selector_options(self.hass, connectable=False) if ble_possible else []

        schema_dict: dict = {
            vol.Required(CONF_HOST, default=reconfigure_entry.data.get(CONF_HOST, "")): str,
            vol.Required(CONF_NAME, default=reconfigure_entry.data.get(CONF_NAME, "BLOOMIN8 E-Ink Canvas")): str,
        }

        if ble_possible:
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
                    vol.Optional(
                        CONF_MAC_ADDRESS,
                        default=reconfigure_entry.data.get(CONF_MAC_ADDRESS, ""),
                    ): str,
                    vol.Optional(
                        CONF_BLE_AUTO_WAKE,
                        default=reconfigure_entry.data.get(
                            CONF_BLE_AUTO_WAKE,
                            DEFAULT_BLE_AUTO_WAKE,
                        ),
                    ): bool,
                }
            )

        schema_dict.update(
            {
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

    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        """Manual setup (IP-based)."""
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

                # Prefer SN as stable identity.
                sn = info.get("sn")
                if sn:
                    await self.async_set_unique_id(sn)
                    self._abort_if_unique_id_configured()

                # If Bluetooth is available and we learned bt_mac, store it for future BLE wakes.
                if _ble_wake_possible(self.hass) and not (user_input.get(CONF_MAC_ADDRESS) or "").strip():
                    bt_mac_norm = info.get("bt_mac_norm")
                    if bt_mac_norm:
                        user_input[CONF_MAC_ADDRESS] = bt_mac_norm

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

        ble_possible = _ble_wake_possible(self.hass)
        coming_from_ble_discovery = bool((self._prefill_mac or "").strip())
        connectable_options = (
            _build_ble_selector_options(self.hass, connectable=True)
            if ble_possible and not coming_from_ble_discovery
            else []
        )
        any_options = (
            _build_ble_selector_options(self.hass, connectable=False)
            if ble_possible and not coming_from_ble_discovery
            else []
        )

        schema_dict: dict = {
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_NAME, default="BLOOMIN8 E-Ink Canvas"): str,
        }

        if ble_possible:
            # Prefer connectable devices for wake (GATT write requires a connection).
            # If this flow was started from a Bluetooth-discovered device, we already
            # have the correct MAC and don't need another dropdown.
            if not coming_from_ble_discovery:
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
                }
            )

        schema_dict.update(
            {
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
            step_id="manual",
            data_schema=vol.Schema(schema_dict),
            errors=errors,
        )

class CannotConnect(HomeAssistantError):
    """Error to indicate we cannot connect."""

class InvalidAuth(HomeAssistantError):
    """Error to indicate there is invalid auth."""
