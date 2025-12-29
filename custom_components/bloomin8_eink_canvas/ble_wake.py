"""BLE wake helper for BLOOMIN8 E-Ink Canvas.

Centralizes the wake-pulse logic used by:
- API client auto/forced wake
- Wake (Bluetooth) button
- Config flow pre-wake during setup/reconfigure

Home Assistant specifics:
- Uses HA Bluetooth cache to resolve BLEDevice (local adapter and/or proxies).
- Must be async and must not block the event loop.
- Must be tolerant: BLE can be flaky; failures should be best-effort.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant

from .const import (
    BLE_WAKE_CHAR_UUIDS,
    BLE_WAKE_PAYLOAD_ON,
    BLE_WAKE_PAYLOAD_OFF,
    BLE_WAKE_PULSE_GAP_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class BleWakeResult:
    attempted: bool
    ok: bool
    mode: str
    connect_dt: float | None = None
    write_dt: float | None = None
    release_dt: float | None = None
    disconnect_dt: float | None = None
    disconnect_timed_out: bool = False
    used_char: str | None = None
    used_write_response: bool | None = None
    release_ok: bool | None = None
    error: str | None = None


async def async_ble_wake(
    hass: HomeAssistant,
    mac_address: str,
    *,
    log_prefix: str = "BLE wake",
    connect_timeout: float = 20,
    max_attempts: int = 4,
    write_timeout: float = 2,
    disconnect_timeout: float = 5,
) -> BleWakeResult:
    """Best-effort: send wake pulse to the device via BLE.

    Returns a BleWakeResult with timings and the chosen characteristic.
    """

    mac = (mac_address or "").strip()
    if not mac:
        return BleWakeResult(attempted=False, ok=False, mode="none", error="missing mac")

    ble_device = bluetooth.async_ble_device_from_address(hass, mac, connectable=True)
    if not ble_device:
        return BleWakeResult(
            attempted=False,
            ok=False,
            mode="none",
            error="not found in HA Bluetooth cache or not connectable",
        )

    # Import bleak lazily.
    try:
        from bleak import BleakClient  # type: ignore
    except ImportError:
        _LOGGER.warning("%s enabled but 'bleak' is not available; skipping", log_prefix)
        return BleWakeResult(attempted=False, ok=False, mode="none", error="bleak not available")

    t0 = time.monotonic()

    # One-line summary metrics for debugging "sticky" BLE connections.
    connect_dt: float | None = None
    write_dt: float | None = None
    release_dt: float | None = None
    disconnect_dt: float | None = None
    disconnect_timed_out = False
    used_char: str | None = None
    used_write_response: bool | None = None
    release_ok: bool | None = None

    # Prefer HA-recommended connector when available.
    try:
        from bleak_retry_connector import (  # type: ignore
            BleakClientWithServiceCache,
            establish_connection,
        )

        mode = "bleak_retry_connector"
        client = None
        ok = False
        err_str: str | None = None
        try:
            t_connect_start = time.monotonic()
            client = await asyncio.wait_for(
                establish_connection(
                    BleakClientWithServiceCache,
                    ble_device,
                    name=getattr(ble_device, "name", None) or mac,
                    max_attempts=int(max_attempts),
                ),
                timeout=float(connect_timeout),
            )
            connect_dt = time.monotonic() - t_connect_start

            last_err: Exception | None = None
            for char_uuid in BLE_WAKE_CHAR_UUIDS:
                try:
                    t_write_start = time.monotonic()
                    try:
                        await asyncio.wait_for(
                            client.write_gatt_char(
                                char_uuid,
                                BLE_WAKE_PAYLOAD_ON,
                                response=True,
                            ),
                            timeout=float(write_timeout),
                        )
                        used_write_response = True
                    except asyncio.TimeoutError:
                        _LOGGER.debug(
                            "%s write timed out waiting for response; retrying without response (mac=%s, char=%s)",
                            log_prefix,
                            mac,
                            char_uuid,
                        )
                        await client.write_gatt_char(
                            char_uuid,
                            BLE_WAKE_PAYLOAD_ON,
                            response=False,
                        )
                        used_write_response = False
                    write_dt = time.monotonic() - t_write_start

                    # Release pulse (0x00) best-effort.
                    release_ok = False
                    try:
                        if BLE_WAKE_PULSE_GAP_SECONDS > 0:
                            await asyncio.sleep(BLE_WAKE_PULSE_GAP_SECONDS)
                        t_release_start = time.monotonic()
                        await client.write_gatt_char(
                            char_uuid,
                            BLE_WAKE_PAYLOAD_OFF,
                            response=False,
                        )
                        release_dt = time.monotonic() - t_release_start
                        release_ok = True
                    except Exception as err:  # noqa: BLE001 - best-effort
                        _LOGGER.debug(
                            "%s release write failed (mac=%s, char=%s, err=%s: %s)",
                            log_prefix,
                            mac,
                            char_uuid,
                            type(err).__name__,
                            err,
                        )

                    used_char = char_uuid
                    last_err = None
                    break
                except Exception as err:  # noqa: BLE001 - best-effort fallback chain
                    last_err = err

            if last_err is not None:
                raise last_err
            ok = True
        except Exception as err:
            ok = False
            err_str = f"{type(err).__name__}: {err}"
        finally:
            if client is not None:
                t_disconnect_start = time.monotonic()
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=float(disconnect_timeout))
                except asyncio.TimeoutError:
                    disconnect_timed_out = True
                disconnect_dt = time.monotonic() - t_disconnect_start
        return BleWakeResult(
            attempted=True,
            ok=ok,
            mode=mode,
            connect_dt=connect_dt,
            write_dt=write_dt,
            release_dt=release_dt,
            disconnect_dt=disconnect_dt,
            disconnect_timed_out=disconnect_timed_out,
            used_char=used_char,
            used_write_response=used_write_response,
            release_ok=release_ok,
            error=err_str,
        )
    except ImportError:
        # Fallback to plain BleakClient.
        mode = "bleak"
        t_connect_start = time.monotonic()
        try:
            async with BleakClient(ble_device) as client:
                connect_dt = time.monotonic() - t_connect_start
                if not client.is_connected:
                    return BleWakeResult(
                        attempted=True,
                        ok=False,
                        mode=mode,
                        connect_dt=connect_dt,
                        error="failed to connect",
                    )

                last_err: Exception | None = None
                for char_uuid in BLE_WAKE_CHAR_UUIDS:
                    try:
                        t_write_start = time.monotonic()
                        try:
                            await asyncio.wait_for(
                                client.write_gatt_char(
                                    char_uuid,
                                    BLE_WAKE_PAYLOAD_ON,
                                    response=True,
                                ),
                                timeout=float(write_timeout),
                            )
                            used_write_response = True
                        except asyncio.TimeoutError:
                            _LOGGER.debug(
                                "%s write timed out waiting for response; retrying without response (fallback BleakClient) (mac=%s, char=%s)",
                                log_prefix,
                                mac,
                                char_uuid,
                            )
                            await client.write_gatt_char(
                                char_uuid,
                                BLE_WAKE_PAYLOAD_ON,
                                response=False,
                            )
                            used_write_response = False
                        write_dt = time.monotonic() - t_write_start

                        release_ok = False
                        try:
                            if BLE_WAKE_PULSE_GAP_SECONDS > 0:
                                await asyncio.sleep(BLE_WAKE_PULSE_GAP_SECONDS)
                            t_release_start = time.monotonic()
                            await client.write_gatt_char(
                                char_uuid,
                                BLE_WAKE_PAYLOAD_OFF,
                                response=False,
                            )
                            release_dt = time.monotonic() - t_release_start
                            release_ok = True
                        except Exception as err:  # noqa: BLE001 - best-effort
                            _LOGGER.debug(
                                "%s release write failed (fallback BleakClient) (mac=%s, char=%s, err=%s: %s)",
                                log_prefix,
                                mac,
                                char_uuid,
                                type(err).__name__,
                                err,
                            )

                        used_char = char_uuid
                        last_err = None
                        break
                    except Exception as err:  # noqa: BLE001 - best-effort fallback chain
                        last_err = err

                if last_err is not None:
                    raise last_err

            return BleWakeResult(
                attempted=True,
                ok=True,
                mode=mode,
                connect_dt=connect_dt,
                write_dt=write_dt,
                release_dt=release_dt,
                disconnect_dt=None,
                used_char=used_char,
                used_write_response=used_write_response,
                release_ok=release_ok,
            )
        except Exception as err:
            return BleWakeResult(
                attempted=True,
                ok=False,
                mode=mode,
                connect_dt=connect_dt,
                write_dt=write_dt,
                release_dt=release_dt,
                used_char=used_char,
                used_write_response=used_write_response,
                release_ok=release_ok,
                error=f"{type(err).__name__}: {err}",
            )
        finally:
            # BleakClient context manager handles disconnect.
            pass
    finally:
        _ = time.monotonic() - t0
