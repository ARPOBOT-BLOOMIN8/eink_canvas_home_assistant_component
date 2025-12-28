"""API Client for BLOOMIN8 E-Ink Canvas.

This client implements the official Bloomin8 E-Ink Canvas API as documented in openapi.yaml.
The device returns some responses with incorrect content-types (e.g., text/json, text/javascript
instead of application/json), so we handle JSON parsing manually where needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any
from urllib.parse import urlencode

import aiohttp
import async_timeout

from homeassistant.components import bluetooth
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ENDPOINT_SHOW,
    ENDPOINT_SHOW_NEXT,
    ENDPOINT_SLEEP,
    ENDPOINT_REBOOT,
    ENDPOINT_CLEAR_SCREEN,
    ENDPOINT_SETTINGS,
    ENDPOINT_WHISTLE,
    ENDPOINT_DEVICE_INFO,
    ENDPOINT_UPLOAD,
    ENDPOINT_UPLOAD_MULTI,
    ENDPOINT_DATA_UPLOAD,
    ENDPOINT_DELETE_IMAGE,
    ENDPOINT_GALLERY,
    ENDPOINT_PLAYLIST,
    ENDPOINT_PLAYLIST_LIST,
    ENDPOINT_STATUS,
    BLE_WAKE_CHAR_UUIDS,
    BLE_WAKE_PAYLOAD_ON,
    BLE_WAKE_PAYLOAD_OFF,
    BLE_WAKE_PULSE_GAP_SECONDS,
)

_LOGGER = logging.getLogger(__name__)


class EinkCanvasApiClient:
    """API client for BLOOMIN8 E-Ink Canvas device."""

    def __init__(
        self,
        hass: HomeAssistant,
        host: str,
        *,
        mac_address: str | None = None,
        ble_auto_wake: bool = False,
        ble_wake_wait_seconds: int = 10,
        http_online_timeout_seconds: int = 30,
    ) -> None:
        """Initialize the API client."""
        self._hass = hass
        self._host = host
        self._session = async_get_clientsession(hass)

        self._mac_address = (mac_address or "").strip()
        self._ble_auto_wake = bool(ble_auto_wake)
        self._ble_wake_wait_seconds = int(ble_wake_wait_seconds)
        self._http_online_timeout_seconds = int(http_online_timeout_seconds)

        # Prevent multiple concurrent wake attempts.
        self._ble_wake_lock = asyncio.Lock()
        # Rate limit: avoid spamming BLE wake on repeated calls.
        self._last_ble_wake_attempt = 0.0

        # Some device firmwares send invalid HTTP response headers (e.g., duplicate
        # Content-Length) on /upload responses. Once detected, we switch to a lenient
        # raw-socket implementation for subsequent uploads.
        self._upload_requires_lenient_http = False

        # Track last-known request health per endpoint to avoid log spam during polling.
        # Keyed by a short identifier like "GET /deviceInfo".
        self._request_last_ok: dict[str, bool] = {}

    def _request_key(self, method: str, endpoint: str) -> str:
        return f"{method.upper()} {endpoint}"

    @staticmethod
    def _sanitize_gallery(gallery: str) -> str:
        """Make gallery names safe for the device API."""
        g = (gallery or "").strip()
        if not g:
            return "default"
        g = re.sub(r"\s+", "_", g)
        g = re.sub(r"[^A-Za-z0-9._-]", "_", g)
        g = re.sub(r"_+", "_", g).strip("._-")
        return g or "default"

    @staticmethod
    def _sanitize_filename(filename: str) -> str:
        """Make filenames safe for the device API.

        The device firmware appears to reject filenames with spaces and certain
        characters. We also default to .jpg because we always upload JPEG bytes.
        """
        name = (filename or "").strip()
        name = name.replace("/", "_").replace("\\", "_")
        name = re.sub(r"\s+", "_", name)
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)
        name = re.sub(r"_+", "_", name).strip("._-")

        if not name:
            name = f"ha_{int(time.time() * 1000)}"

        # Enforce a JPEG extension (we upload JPEG bytes).
        lower = name.lower()
        if lower.endswith(".jpeg"):
            name = name[:-5] + ".jpg"
        elif not lower.endswith(".jpg"):
            # Strip any other extension and replace with .jpg
            if "." in name:
                name = name.rsplit(".", 1)[0]
            name = f"{name}.jpg"

        # Best-effort limit (some firmwares are picky).
        if len(name) > 80:
            stem, ext = name.rsplit(".", 1)
            name = f"{stem[:75]}.{ext}"
        return name

    @staticmethod
    def _truncate(text: str, limit: int = 300) -> str:
        text = (text or "").replace("\n", "\\n").replace("\r", "\\r")
        if len(text) <= limit:
            return text
        return f"{text[:limit]}…"

    def _log_request_state_change(
        self,
        key: str,
        *,
        ok: bool,
        fail_level: int,
        fail_message: str,
        recover_level: int = logging.INFO,
        recover_message: str | None = None,
    ) -> None:
        """Log only when a request transitions between success and failure.

        - First failure (ok->fail): log at fail_level.
        - Continued failure (fail->fail): no log.
        - Recovery (fail->ok): log once at recover_level.
        """
        prev_ok = self._request_last_ok.get(key, True)
        self._request_last_ok[key] = ok

        if not ok and prev_ok:
            _LOGGER.log(fail_level, "%s", fail_message)
        elif ok and not prev_ok and recover_message:
            _LOGGER.log(recover_level, "%s", recover_message)

    async def _async_http_ping(self, timeout_seconds: int = 3) -> bool:
        """Quickly check if device is reachable over HTTP.

        This is a lightweight *connectivity* probe used to avoid noisy error logs
        when the battery-powered device is asleep/offline.

        Notes:
        - We treat *any* HTTP response as "reachable" (even 404/500), because it
          still proves the device answered over TCP/HTTP.
        - Only connection/timeouts count as "offline".
        """
        try:
            timeout_seconds = max(1, int(timeout_seconds))
        except (TypeError, ValueError):
            timeout_seconds = 3

        try:
            async with async_timeout.timeout(timeout_seconds):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_STATUS}"
                ) as _response:
                    # Any response means the host is reachable.
                    return True
        except Exception:
            return False

    async def get_image_bytes(self, image_path: str, *, wake: bool = False, timeout: int = 10) -> bytes | None:
        """Fetch raw image bytes from the device.

        This is used by the media_player proxy implementation to avoid handing
        out direct device URLs to clients.

        Important behavior:
        - By default (wake=False) this will NOT wake the device via BLE.
        - If the device is asleep/offline, returns None without error spam.
        - Only accepts absolute device paths (must start with "/") to avoid SSRF.
        """
        if not image_path or not isinstance(image_path, str):
            return None
        if not image_path.startswith("/"):
            _LOGGER.debug("Refusing to fetch image bytes for non-path value: %s", image_path)
            return None

        # Never wake unless explicitly requested.
        if wake:
            await self.async_ensure_awake()
        else:
            if not await self._async_http_ping():
                return None

        url = f"http://{self._host}{image_path}"
        try:
            async with async_timeout.timeout(timeout):
                async with self._session.get(url) as response:
                    if response.status == 200:
                        _LOGGER.debug(
                            "Fetched image bytes from device (path=%s, content_type=%s, size=%s)",
                            image_path,
                            response.content_type,
                            response.content_length,
                        )
                        return await response.read()

                    body = self._truncate(await response.text())
                    _LOGGER.debug(
                        "Image fetch failed (path=%s) status=%s content_type=%s body='%s'",
                        image_path,
                        response.status,
                        response.content_type,
                        body,
                    )
                    return None
        except Exception as err:
            _LOGGER.debug(
                "Image fetch error (path=%s) err=%s: %s",
                image_path,
                type(err).__name__,
                err,
            )
            return None

    async def async_ensure_awake(self) -> None:
        """Best-effort: wake device via BLE (if enabled) and wait until HTTP is reachable.

        - If device is already reachable, returns immediately.
        - If BLE wake isn't available (no MAC, not connectable, bleak missing), it will just return.
        - Rate-limited to avoid repeated wake attempts in tight loops.
        """
        if not self._ble_auto_wake:
            return
        if not self._mac_address:
            return

        # Fast path: already online.
        if await self._async_http_ping():
            return

        now = time.monotonic()
        # 30s cooldown by default.
        if (now - self._last_ble_wake_attempt) < 30:
            _LOGGER.debug(
                "BLE auto-wake skipped due to cooldown (host=%s, mac=%s, cooldown_remaining=%.1fs)",
                self._host,
                self._mac_address,
                30 - (now - self._last_ble_wake_attempt),
            )
            return

        async with self._ble_wake_lock:
            # Another task may have woken it while we waited for the lock.
            if await self._async_http_ping():
                return

            now = time.monotonic()
            if (now - self._last_ble_wake_attempt) < 30:
                _LOGGER.debug(
                    "BLE auto-wake skipped due to cooldown after lock wait (host=%s, mac=%s, cooldown_remaining=%.1fs)",
                    self._host,
                    self._mac_address,
                    30 - (now - self._last_ble_wake_attempt),
                )
                return
            self._last_ble_wake_attempt = now

            ble_device = bluetooth.async_ble_device_from_address(
                self._hass,
                self._mac_address,
                connectable=True,
            )
            if not ble_device:
                _LOGGER.debug(
                    "BLE auto-wake skipped: device %s not found in HA Bluetooth cache or not connectable",
                    self._mac_address,
                )
                return

            try:
                from bleak import BleakClient  # type: ignore
            except ImportError:
                _LOGGER.warning(
                    "BLE auto-wake enabled but 'bleak' is not available; skipping BLE wake"
                )
                return

            _LOGGER.debug("Auto-waking device via BLE: %s", self._mac_address)
            attempted = False
            t0 = time.monotonic()

            # One-line summary metrics for debugging "sticky" BLE connections.
            ble_connect_dt: float | None = None
            ble_write_dt: float | None = None
            ble_release_write_dt: float | None = None
            ble_disconnect_dt: float | None = None
            ble_disconnect_timed_out = False
            ble_used_char: str | None = None
            ble_used_write_response: bool | None = None
            ble_release_ok: bool | None = None
            ble_mode = "bleak_retry_connector"
            try:
                attempted = True

                # Prefer HA-recommended connector when available for more reliable connects.
                try:
                    from bleak_retry_connector import (  # type: ignore
                        BleakClientWithServiceCache,
                        establish_connection,
                    )

                    t_connect_start = time.monotonic()
                    client = await asyncio.wait_for(
                        establish_connection(
                            BleakClientWithServiceCache,
                            ble_device,
                            name=getattr(ble_device, "name", None) or self._mac_address,
                            max_attempts=4,
                        ),
                        timeout=20,
                    )
                    ble_connect_dt = time.monotonic() - t_connect_start
                    _LOGGER.debug(
                        "BLE auto-wake connected (host=%s, mac=%s, dt=%.2fs)",
                        self._host,
                        self._mac_address,
                        ble_connect_dt,
                    )
                    try:
                        last_err: Exception | None = None
                        for char_uuid in BLE_WAKE_CHAR_UUIDS:
                            try:
                                t_write_start = time.monotonic()
                                try:
                                    # Some BLE proxy stacks can end up holding the connection
                                    # longer than expected when waiting for a write response.
                                    # Prefer a short timeout, then fall back to a write without
                                    # response to reduce the chance of "sticky" connections.
                                    await asyncio.wait_for(
                                        client.write_gatt_char(
                                            char_uuid,
                                            BLE_WAKE_PAYLOAD_ON,
                                            response=True,
                                        ),
                                        timeout=2,
                                    )
                                    ble_used_write_response = True
                                except asyncio.TimeoutError:
                                    _LOGGER.debug(
                                        "BLE wake write timed out waiting for response; retrying without response (host=%s, mac=%s, char=%s)",
                                        self._host,
                                        self._mac_address,
                                        char_uuid,
                                    )
                                    await client.write_gatt_char(
                                        char_uuid,
                                        BLE_WAKE_PAYLOAD_ON,
                                        response=False,
                                    )
                                    ble_used_write_response = False
                                ble_write_dt = time.monotonic() - t_write_start

                                # Release pulse: some firmwares require a second write 0x00.
                                ble_release_ok = False
                                try:
                                    if BLE_WAKE_PULSE_GAP_SECONDS > 0:
                                        await asyncio.sleep(BLE_WAKE_PULSE_GAP_SECONDS)
                                    t_release_start = time.monotonic()
                                    await client.write_gatt_char(
                                        char_uuid,
                                        BLE_WAKE_PAYLOAD_OFF,
                                        response=False,
                                    )
                                    ble_release_write_dt = time.monotonic() - t_release_start
                                    ble_release_ok = True
                                except Exception as err:  # noqa: BLE001 - best-effort
                                    _LOGGER.debug(
                                        "BLE wake release write failed (host=%s, mac=%s, char=%s, err=%s: %s)",
                                        self._host,
                                        self._mac_address,
                                        char_uuid,
                                        type(err).__name__,
                                        err,
                                    )

                                ble_used_char = char_uuid
                                _LOGGER.debug(
                                    "BLE auto-wake signal sent (host=%s, mac=%s, char=%s, dt=%.2fs)",
                                    self._host,
                                    self._mac_address,
                                    char_uuid,
                                    ble_write_dt,
                                )
                                last_err = None
                                break
                            except Exception as err:  # noqa: BLE001 - best-effort fallback chain
                                last_err = err
                        if last_err is not None:
                            raise last_err
                    finally:
                        t_disconnect_start = time.monotonic()
                        try:
                            await asyncio.wait_for(client.disconnect(), timeout=5)
                        except asyncio.TimeoutError:
                            ble_disconnect_timed_out = True
                            _LOGGER.debug(
                                "BLE auto-wake disconnect timed out (host=%s, mac=%s)",
                                self._host,
                                self._mac_address,
                            )
                        ble_disconnect_dt = time.monotonic() - t_disconnect_start
                        _LOGGER.debug(
                            "BLE auto-wake disconnected (host=%s, mac=%s, dt=%.2fs, total=%.2fs)",
                            self._host,
                            self._mac_address,
                            ble_disconnect_dt,
                            time.monotonic() - t0,
                        )
                except ImportError:
                    # Fallback to plain BleakClient (may log a warning in newer HA).
                    ble_mode = "bleak"
                    t_connect_start = time.monotonic()
                    async with BleakClient(ble_device) as client:
                        ble_connect_dt = time.monotonic() - t_connect_start
                        _LOGGER.debug(
                            "BLE auto-wake connected (fallback BleakClient) (host=%s, mac=%s, dt=%.2fs)",
                            self._host,
                            self._mac_address,
                            ble_connect_dt,
                        )
                        if not client.is_connected:
                            _LOGGER.warning(
                                "BLE auto-wake: failed to connect to %s",
                                self._mac_address,
                            )
                        else:
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
                                            timeout=2,
                                        )
                                        ble_used_write_response = True
                                    except asyncio.TimeoutError:
                                        _LOGGER.debug(
                                            "BLE wake write timed out waiting for response; retrying without response (fallback BleakClient) (host=%s, mac=%s, char=%s)",
                                            self._host,
                                            self._mac_address,
                                            char_uuid,
                                        )
                                        await client.write_gatt_char(
                                            char_uuid,
                                            BLE_WAKE_PAYLOAD_ON,
                                            response=False,
                                        )
                                        ble_used_write_response = False
                                    ble_write_dt = time.monotonic() - t_write_start

                                    ble_release_ok = False
                                    try:
                                        if BLE_WAKE_PULSE_GAP_SECONDS > 0:
                                            await asyncio.sleep(BLE_WAKE_PULSE_GAP_SECONDS)
                                        t_release_start = time.monotonic()
                                        await client.write_gatt_char(
                                            char_uuid,
                                            BLE_WAKE_PAYLOAD_OFF,
                                            response=False,
                                        )
                                        ble_release_write_dt = time.monotonic() - t_release_start
                                        ble_release_ok = True
                                    except Exception as err:  # noqa: BLE001 - best-effort
                                        _LOGGER.debug(
                                            "BLE wake release write failed (fallback BleakClient) (host=%s, mac=%s, char=%s, err=%s: %s)",
                                            self._host,
                                            self._mac_address,
                                            char_uuid,
                                            type(err).__name__,
                                            err,
                                        )

                                    ble_used_char = char_uuid
                                    _LOGGER.debug(
                                        "BLE auto-wake signal sent (fallback BleakClient) (host=%s, mac=%s, char=%s, dt=%.2fs)",
                                        self._host,
                                        self._mac_address,
                                        char_uuid,
                                        ble_write_dt,
                                    )
                                    last_err = None
                                    break
                                except Exception as err:  # noqa: BLE001 - best-effort fallback chain
                                    last_err = err
                            if last_err is not None:
                                raise last_err
                    _LOGGER.debug(
                        "BLE auto-wake finished (fallback BleakClient) (host=%s, mac=%s, total=%.2fs)",
                        self._host,
                        self._mac_address,
                        time.monotonic() - t0,
                    )
            except Exception as err:
                _LOGGER.warning("BLE auto-wake failed for %s: %s", self._mac_address, err)
            finally:
                if attempted and self._ble_wake_wait_seconds > 0:
                    await asyncio.sleep(self._ble_wake_wait_seconds)

            _LOGGER.debug(
                "BLE auto-wake summary (host=%s, mac=%s, mode=%s, connect_dt=%s, write_dt=%s, release_dt=%s, release_ok=%s, disconnect_dt=%s, disconnect_timeout=%s, char=%s, write_response=%s, total_ble_dt=%.2fs)",
                self._host,
                self._mac_address,
                ble_mode,
                None if ble_connect_dt is None else f"{ble_connect_dt:.2f}s",
                None if ble_write_dt is None else f"{ble_write_dt:.2f}s",
                None if ble_release_write_dt is None else f"{ble_release_write_dt:.2f}s",
                ble_release_ok,
                None if ble_disconnect_dt is None else f"{ble_disconnect_dt:.2f}s",
                ble_disconnect_timed_out,
                ble_used_char,
                ble_used_write_response,
                time.monotonic() - t0,
            )

            # Wait for device to come online over Wi-Fi.
            start = time.monotonic()
            while (time.monotonic() - start) < self._http_online_timeout_seconds:
                if await self._async_http_ping():
                    _LOGGER.debug(
                        "BLE auto-wake HTTP online (host=%s, mac=%s, http_wait_dt=%.2fs, max_wait=%ss)",
                        self._host,
                        self._mac_address,
                        time.monotonic() - start,
                        self._http_online_timeout_seconds,
                    )
                    return
                await asyncio.sleep(2)

            _LOGGER.warning(
                "BLE auto-wake attempt finished, but device did not come online over HTTP within %ss (%s)",
                self._http_online_timeout_seconds,
                self._host,
            )

    @property
    def host(self) -> str:
        """Return the device host."""
        return self._host

    async def get_status(self, *, wake: bool = False) -> dict[str, Any] | None:
        """Get device status.

        Important behavior:
        - By default (wake=False), this will NOT trigger BLE wake. It will only query
          the endpoint if the device is already reachable over HTTP.
        - When wake=True, we may wake the device via BLE (if configured) before
          attempting the HTTP call.
        """
        key = self._request_key("GET", ENDPOINT_STATUS)
        url = f"http://{self._host}{ENDPOINT_STATUS}"
        try:
            if wake:
                await self.async_ensure_awake()
            else:
                # Polling path: never wake. Only query if already online.
                if not await self._async_http_ping():
                    _LOGGER.debug(
                        "Skipping %s because device is offline/asleep and wake=False (host=%s)",
                        key,
                        self._host,
                    )
                    return None
            async with async_timeout.timeout(10):
                async with self._session.get(url) as response:
                    if response.status == 200:
                        self._log_request_state_change(
                            key,
                            ok=True,
                            fail_level=logging.ERROR,
                            fail_message="",
                            recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                        )
                        return await response.json()

                    body = self._truncate(await response.text())
                    self._log_request_state_change(
                        key,
                        ok=False,
                        fail_level=logging.ERROR,
                        fail_message=(
                            f"HTTP request failed: {key} ({url}) status={response.status} "
                            f"content_type={response.content_type} body='{body}'"
                        ),
                        recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                    )
                    return None
        except Exception as err:
            self._log_request_state_change(
                key,
                ok=False,
                fail_level=logging.ERROR,
                fail_message=(
                    f"HTTP request error: {key} ({url}) err={type(err).__name__}: {err}"
                ),
                recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
            )
            _LOGGER.debug("Error getting status (details)", exc_info=err)
            return None

    async def get_device_info(
        self, *, wake: bool = False, timeout: int | None = None
    ) -> dict[str, Any] | None:
        """Get device information from /deviceInfo endpoint.

        Returns device status including name, version, battery, screen resolution,
        current image, network info, etc. See openapi.yaml for full response schema.
        
        Args:
            wake: If True, attempt BLE wake before HTTP request.
            timeout: Optional custom timeout in seconds (default: 10s).
        """
        key = self._request_key("GET", ENDPOINT_DEVICE_INFO)
        url = f"http://{self._host}{ENDPOINT_DEVICE_INFO}"
        request_timeout = timeout if timeout is not None else 10
        try:
            if wake:
                await self.async_ensure_awake()
            else:
                # Polling path: never wake. Only query if already online.
                # Use a slightly longer ping when the caller provided a custom
                # timeout (e.g. post-BLE-wake refresh): the device may need a
                # couple seconds to bring Wi-Fi back up.
                ping_timeout = min(int(request_timeout), 5)
                if not await self._async_http_ping(timeout_seconds=ping_timeout):
                    _LOGGER.debug(
                        "Skipping %s because device is offline/asleep and wake=False (host=%s)",
                        key,
                        self._host,
                    )
                    return None
            async with async_timeout.timeout(request_timeout):
                async with self._session.get(url) as response:
                    text_response = await response.text()

                    if response.status != 200:
                        body = self._truncate(text_response)
                        self._log_request_state_change(
                            key,
                            ok=False,
                            fail_level=logging.ERROR,
                            fail_message=(
                                f"HTTP request failed: {key} ({url}) status={response.status} "
                                f"content_type={response.content_type} body='{body}'"
                            ),
                            recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                        )
                        return None

                    # 200 OK
                    try:
                        data = json.loads(text_response)
                    except json.JSONDecodeError:
                        # Try to extract JSON from malformed response
                        start = text_response.find("{")
                        end = text_response.rfind("}") + 1
                        if start >= 0 and end > start:
                            try:
                                data = json.loads(text_response[start:end])
                            except json.JSONDecodeError:
                                body = self._truncate(text_response)
                                self._log_request_state_change(
                                    key,
                                    ok=False,
                                    fail_level=logging.WARNING,
                                    fail_message=(
                                        f"Invalid JSON in response: {key} ({url}) "
                                        f"content_type={response.content_type} body='{body}'"
                                    ),
                                    recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                                )
                                return None
                        else:
                            body = self._truncate(text_response)
                            self._log_request_state_change(
                                key,
                                ok=False,
                                fail_level=logging.WARNING,
                                fail_message=(
                                    f"Invalid JSON in response: {key} ({url}) "
                                    f"content_type={response.content_type} body='{body}'"
                                ),
                                recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                            )
                            return None

                    # Success path
                    self._log_request_state_change(
                        key,
                        ok=True,
                        fail_level=logging.ERROR,
                        fail_message="",
                        recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
                    )
                    return data
        except Exception as err:
            self._log_request_state_change(
                key,
                ok=False,
                fail_level=logging.ERROR,
                fail_message=(
                    f"HTTP request error: {key} ({url}) err={type(err).__name__}: {err}"
                ),
                recover_message=f"Device HTTP endpoint recovered: {key} ({self._host})",
            )
            _LOGGER.debug("Error getting device info (details)", exc_info=err)
            return None

    async def show_next(self) -> bool:
        """Show next image."""
        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SHOW_NEXT}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Successfully sent showNext command")
                        return True
                    _LOGGER.error("ShowNext failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.debug("Error in showNext: %s", err)
            return False

    async def sleep(self) -> bool:
        """Put device to sleep."""
        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SLEEP}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Device sleep command sent successfully")
                        return True
                    _LOGGER.error("Sleep failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.debug("Error in sleep: %s", err)
            return False

    async def reboot(self) -> bool:
        """Reboot device."""
        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_REBOOT}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Device reboot command sent successfully")
                        return True
                    _LOGGER.error("Reboot failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.debug("Error in reboot: %s", err)
            return False

    async def clear_screen(self) -> bool:
        """Clear the screen.
        
        Note: The device takes ~15-20 seconds to physically clear the E-Ink display
        and only responds after the operation completes.
        """
        try:
            await self.async_ensure_awake()
            # E-Ink refresh takes ~15-20 seconds; use a generous timeout.
            async with async_timeout.timeout(30):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_CLEAR_SCREEN}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Screen cleared successfully")
                        return True
                    _LOGGER.error("Clear screen failed with status %s", response.status)
                    return False
        except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as err:
            # The device may still process the request even if the response is broken
            # or arrives too late. Treat as a warning and try a quick ping.
            _LOGGER.warning(
                "Clear screen did not return a valid response (host=%s, ble_auto_wake=%s, has_mac=%s): %s. "
                "Command may have been processed by the device.",
                self._host,
                self._ble_auto_wake,
                bool(self._mac_address),
                err,
            )
            _LOGGER.debug("Clear screen error details", exc_info=err)

            # If the device is reachable right after, assume it likely processed it.
            if await self._async_http_ping():
                return True
            return False
        except Exception as err:
            _LOGGER.exception(
                "Unexpected error in clear screen (host=%s, ble_auto_wake=%s, has_mac=%s): %r",
                self._host,
                self._ble_auto_wake,
                bool(self._mac_address),
                err,
            )
            return False

    async def whistle(self) -> bool:
        """Send keep-alive signal."""
        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(10):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_WHISTLE}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Whistle command sent successfully")
                        return True
                    _LOGGER.error("Whistle failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.debug("Error in whistle: %s", err)
            return False

    async def update_settings(self, settings: dict[str, Any]) -> bool:
        """Update device settings."""
        if not settings:
            _LOGGER.warning("No settings parameters provided")
            return False

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SETTINGS}",
                    json=settings,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Settings updated successfully: %s", settings)
                        return True
                    _LOGGER.error("Settings update failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.debug("Error in update settings: %s", err)
            return False

    async def show_image(
        self,
        image_path: str,
        play_type: int = 0,
        dither: int | None = None,
        duration: int = 99999
    ) -> bool:
        """Show image using /show API with full path.

        Args:
            image_path: Path to image (e.g., "/gallerys/default/image.jpg")
            play_type: 0=single image, 1=gallery slideshow, 2=playlist
            dither: Optional dithering algorithm (0=Floyd-Steinberg, 1=JJN)
            duration: Display duration in seconds (default: 99999)
        """
        try:
            # Parse image_path to extract gallery and filename
            # Format: "/gallerys/{gallery}/{filename}"
            parts = image_path.strip("/").split("/")
            if len(parts) >= 3 and parts[0] == "gallerys":
                gallery = parts[1]
                filename = parts[2]
            else:
                # Fallback for unexpected format
                gallery = "default"
                filename = image_path.split("/")[-1]

            return await self.show_image_by_name(
                filename,
                gallery,
                play_type,
                playlist=None,
                dither=dither,
                duration=duration,
            )
        except Exception as err:
            _LOGGER.error("Error showing image: %s", err)
            return False

    async def show_image_by_name(
        self,
        filename: str,
        gallery: str = "default",
        play_type: int = 0,
        playlist: str | None = None,
        dither: int | None = None,
        duration: int = 99999
    ) -> bool:
        """Show image using /show API with separate filename and gallery.

        Args:
            filename: Image filename (e.g., "image.jpg")
            gallery: Gallery name (default: "default")
            play_type: 0=single image, 1=gallery slideshow, 2=playlist
            dither: Optional dithering algorithm (0=Floyd-Steinberg, 1=JJN)
            duration: Display duration in seconds (default: 99999)
        """
        try:
            await self.async_ensure_awake()
            show_data: dict[str, Any] = {"play_type": play_type}

            if play_type == 0:
                # Single image mode: requires full path
                show_data["image"] = f"/gallerys/{gallery}/{filename}"
            elif play_type == 1:
                # Gallery slideshow mode: requires gallery, duration, and filename only
                show_data["image"] = filename
                show_data["gallery"] = gallery
                show_data["duration"] = duration
            elif play_type == 2:
                # Playlist mode: requires playlist name. Optionally provide an image
                # path to display immediately.
                if not playlist:
                    _LOGGER.error("Playlist mode (play_type=2) requires 'playlist' parameter")
                    return False
                show_data["playlist"] = playlist
                show_data["image"] = f"/gallerys/{gallery}/{filename}"

            if dither is not None:
                show_data["dither"] = dither

            _LOGGER.debug("Showing image - gallery: %s, filename: %s, data: %s", gallery, filename, show_data)

            async with self._session.post(
                f"http://{self._host}{ENDPOINT_SHOW}",
                json=show_data
            ) as response:
                if response.status == 200:
                    _LOGGER.debug("Successfully displayed image: %s/%s", gallery, filename)
                    return True
                response_text = await response.text()
                _LOGGER.error(
                    "Failed to show image: %s - %s",
                    response.status,
                    response_text
                )
                return False
        except Exception as err:
            _LOGGER.error("Error showing image: %s", err)
            return False

    async def show_playlist(
        self,
        playlist: str,
        *,
        image: str | None = None,
        dither: int | None = None,
        timeout: int = 30,
    ) -> bool:
        """Start playlist playback via /show.

        API docs:
            POST /show
            Body: {"play_type":2, "playlist":"...", "image": optional, "dither": optional}
        """
        if not playlist:
            _LOGGER.error("show_playlist called without playlist name")
            return False

        try:
            await self.async_ensure_awake()
            show_data: dict[str, Any] = {"play_type": 2, "playlist": playlist}
            if image:
                show_data["image"] = image
            if dither is not None:
                show_data["dither"] = dither

            async with async_timeout.timeout(timeout):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SHOW}",
                    json=show_data,
                ) as response:
                    if response.status == 200:
                        _LOGGER.debug("Started playlist playback: %s", playlist)
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Failed to start playlist playback: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error starting playlist playback: %s", err)
            return False

    async def upload_image(
        self,
        image_data: bytes,
        filename: str,
        gallery: str = "default",
        show_now: bool = False,
        max_retries: int = 3
    ) -> str | None:
        """Upload image to device via /upload endpoint.

        Args:
            image_data: JPEG image bytes
            filename: Filename to save as
            gallery: Gallery name (default: "default")
            show_now: Display immediately after upload (1) or not (0)
            max_retries: Number of retry attempts

        Returns:
            Full image path (e.g., "/gallerys/default/image.jpg") or None on failure

        Note:
            The device returns {"status":100, "path":"/gallerys/default/"} with
            content-type text/javascript. We must append the filename to get the full path.
        """
        filename = self._sanitize_filename(filename)
        gallery = self._sanitize_gallery(gallery)

        for attempt in range(max_retries):
            try:
                await self.async_ensure_awake()

                # If we've already detected broken HTTP responses from the device,
                # skip aiohttp and go straight to the lenient implementation.
                if self._upload_requires_lenient_http:
                    uploaded_path = await self._upload_image_lenient_http(
                        image_data=image_data,
                        filename=filename,
                        gallery=gallery,
                        show_now=show_now,
                        timeout=30,
                    )
                    return uploaded_path

                form = aiohttp.FormData()
                form.add_field(
                    "image",
                    image_data,
                    filename=filename,
                    content_type="image/jpeg"
                )

                async with async_timeout.timeout(30):
                    async with self._session.post(
                        f"http://{self._host}{ENDPOINT_UPLOAD}",
                        params={
                            "filename": filename,
                            "gallery": gallery,
                            "show_now": 1 if show_now else 0,
                        },
                        data=form,
                    ) as response:
                        if response.status == 200:
                            response_text = await response.text()

                            try:
                                result = json.loads(response_text)
                                _LOGGER.debug("Upload response: %s", result)
                                # Device firmwares vary: some return a directory path
                                # ("/gallerys/default/") and some return the full image
                                # path ("/gallerys/default/file.jpg"). Handle both.
                                path = result.get("path")
                                if isinstance(path, str) and path:
                                    if path.endswith("/"):
                                        return f"{path}{filename}"
                                    return path
                                return f"/gallerys/{gallery}/{filename}"
                            except json.JSONDecodeError as e:
                                # Fallback to default path construction
                                _LOGGER.warning("Failed to parse upload response: %s", e)
                                image_path = f"/gallerys/{gallery}/{filename}"
                                _LOGGER.debug("Using default path: %s", image_path)
                                return image_path

                        response_text = await response.text()
                        _LOGGER.error("Upload failed: %s - %s", response.status, response_text)
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as err:
                # Some device firmware versions respond with invalid HTTP headers
                # (duplicate Content-Length). aiohttp rejects that response.
                # Work around by doing the upload via a tiny raw-socket HTTP client.
                if "Duplicate Content-Length" in str(err):
                    # Remember for next time to avoid repeated warnings.
                    self._upload_requires_lenient_http = True
                    _LOGGER.warning(
                        "Upload hit invalid HTTP response (%s). Falling back to lenient upload implementation.",
                        err,
                    )
                    try:
                        uploaded_path = await self._upload_image_lenient_http(
                            image_data=image_data,
                            filename=filename,
                            gallery=gallery,
                            show_now=show_now,
                            timeout=30,
                        )
                        if uploaded_path:
                            _LOGGER.debug("Lenient upload succeeded: %s", uploaded_path)
                            return uploaded_path
                    except Exception as fallback_err:
                        _LOGGER.error("Lenient upload fallback failed: %s", fallback_err)

                if attempt < max_retries - 1:
                    wait_time = 2 ** attempt
                    _LOGGER.warning(
                        "Upload attempt %d/%d failed: %s. Retrying in %ds...",
                        attempt + 1, max_retries, err, wait_time
                    )
                    await asyncio.sleep(wait_time)
                else:
                    _LOGGER.error("Upload failed after %d attempts: %s", max_retries, err)
                    return None
            except Exception as err:
                _LOGGER.exception("Unexpected upload error: %s", err)
                return None

        return None

    def _split_host_port(self) -> tuple[str, int]:
        """Split configured host into (host, port).

        The integration stores host as a string (typically an IP). Users may also
        provide host:port.
        """
        host = str(self._host).strip()

        # Very small helper; IPv6 not expected for this device.
        if host.count(":") == 1 and not host.startswith("["):
            h, p = host.split(":", 1)
            try:
                return h.strip(), int(p)
            except ValueError:
                return host, 80
        return host, 80

    @staticmethod
    def _build_multipart_image_body(*, image_data: bytes, filename: str, boundary: str) -> bytes:
        safe_filename = (filename or "image.jpg").replace('"', "")
        prefix = (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"image\"; filename=\"{safe_filename}\"\r\n"
            "Content-Type: image/jpeg\r\n\r\n"
        ).encode("utf-8")
        suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
        return prefix + (image_data or b"") + suffix

    async def _read_http_response_lenient(
        self,
        reader: asyncio.StreamReader,
        *,
        timeout: int,
        header_limit: int = 64 * 1024,
    ) -> tuple[int, dict[str, str], bytes]:
        """Read HTTP/1.1 response leniently.

        - Allows duplicate headers (keeps the first value).
        - Supports Content-Length and basic chunked decoding.
        """

        async def _read_until_delim(delim: bytes) -> bytes:
            buf = bytearray()
            while True:
                chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
                if not chunk:
                    break
                buf += chunk
                if delim in buf:
                    break
                if len(buf) > header_limit:
                    raise ConnectionError("HTTP header too large")
            return bytes(buf)

        raw = await _read_until_delim(b"\r\n\r\n")
        if b"\r\n\r\n" not in raw:
            raise ConnectionError("Incomplete HTTP response headers")

        header_part, rest = raw.split(b"\r\n\r\n", 1)
        header_lines = header_part.split(b"\r\n")
        if not header_lines:
            raise ConnectionError("Empty HTTP response")

        # Status line
        try:
            status_line = header_lines[0].decode("iso-8859-1")
            # e.g. HTTP/1.1 200 OK
            status = int(status_line.split(" ", 2)[1])
        except Exception as err:
            raise ConnectionError(f"Invalid HTTP status line: {err}") from err

        headers: dict[str, str] = {}
        for line in header_lines[1:]:
            if not line or b":" not in line:
                continue
            k, v = line.split(b":", 1)
            key = k.decode("iso-8859-1").strip().lower()
            val = v.decode("iso-8859-1").strip()
            headers.setdefault(key, val)

        body = bytearray(rest)

        te = headers.get("transfer-encoding", "").lower()
        if "chunked" in te:
            # Basic chunked decoding
            while True:
                line = await asyncio.wait_for(reader.readline(), timeout=timeout)
                if not line:
                    break
                chunk_size_str = line.strip().split(b";", 1)[0]
                try:
                    chunk_size = int(chunk_size_str, 16)
                except ValueError as err:
                    raise ConnectionError(f"Invalid chunk size: {chunk_size_str!r}") from err
                if chunk_size == 0:
                    # Consume trailing CRLF and any trailers (best-effort)
                    await asyncio.wait_for(reader.readline(), timeout=timeout)
                    break
                chunk = await asyncio.wait_for(reader.readexactly(chunk_size), timeout=timeout)
                body += chunk
                # Consume CRLF
                await asyncio.wait_for(reader.readexactly(2), timeout=timeout)
            return status, headers, bytes(body)

        cl = headers.get("content-length")
        if cl is not None:
            try:
                total = int(cl)
            except ValueError:
                total = None
            if total is not None:
                missing = total - len(body)
                if missing > 0:
                    body += await asyncio.wait_for(reader.readexactly(missing), timeout=timeout)
                return status, headers, bytes(body[:total])

        # No length known: read until EOF
        while True:
            chunk = await asyncio.wait_for(reader.read(1024), timeout=timeout)
            if not chunk:
                break
            body += chunk
        return status, headers, bytes(body)

    async def _upload_image_lenient_http(
        self,
        *,
        image_data: bytes,
        filename: str,
        gallery: str,
        show_now: bool,
        timeout: int,
    ) -> str | None:
        host, port = self._split_host_port()

        query = urlencode(
            {
                "filename": filename,
                "gallery": gallery,
                "show_now": 1 if show_now else 0,
            }
        )
        path = f"{ENDPOINT_UPLOAD}?{query}"

        boundary = f"----ha-bloomin8-{int(time.time() * 1000)}"
        body = self._build_multipart_image_body(
            image_data=image_data,
            filename=filename,
            boundary=boundary,
        )

        request_lines = [
            f"POST {path} HTTP/1.1",
            f"Host: {host}",
            "User-Agent: homeassistant-bloomin8",
            "Accept: */*",
            "Connection: close",
            f"Content-Type: multipart/form-data; boundary={boundary}",
            f"Content-Length: {len(body)}",
        ]
        request = ("\r\n".join(request_lines) + "\r\n\r\n").encode("utf-8") + body

        reader: asyncio.StreamReader
        writer: asyncio.StreamWriter
        reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=5,
        )
        try:
            writer.write(request)
            await asyncio.wait_for(writer.drain(), timeout=timeout)

            status, _headers, resp_body = await self._read_http_response_lenient(
                reader,
                timeout=timeout,
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

        if status != 200:
            _LOGGER.error("Lenient upload failed: HTTP %s, body=%s", status, resp_body[:500])
            return None

        # Device returns JSON-ish body (sometimes with wrong content-type).
        try:
            text = resp_body.decode("utf-8", errors="replace")
            result = json.loads(text)
            path = result.get("path")
            if isinstance(path, str) and path:
                if path.endswith("/"):
                    return f"{path}{filename}"
                return path
            return f"/gallerys/{gallery}/{filename}"
        except Exception:
            # Fallback to a predictable path if parsing fails.
            return f"/gallerys/{gallery}/{filename}"

    async def get_galleries(self) -> list[dict[str, Any]]:
        """Get list of all galleries via /gallery/list endpoint.

        Returns:
            List of gallery objects with 'name' field, e.g., [{"name": "default"}]

        Note:
            Device returns content-type text/json instead of application/json.
        """
        try:
            await self.async_ensure_awake()
            async with self._session.get(
                f"http://{self._host}/gallery/list"
            ) as response:
                if response.status == 200:
                    text_response = await response.text()
                    try:
                        return json.loads(text_response)
                    except json.JSONDecodeError as err:
                        _LOGGER.error("Failed to parse galleries response: %s", err)
                return []
        except Exception as err:
            _LOGGER.error("Error getting galleries: %s", err)
            return []

    async def get_gallery_images(
        self,
        gallery_name: str,
        offset: int = 0,
        limit: int = 100
    ) -> dict[str, Any]:
        """Get paginated list of images from a gallery via /gallery endpoint.

        Args:
            gallery_name: Gallery name to query
            offset: Starting index for pagination
            limit: Number of items per page

        Returns:
            Dict with 'data' (list of images), 'total', 'offset', 'limit'
            Each image has 'name', 'size', 'time' fields.
        """
        try:
            await self.async_ensure_awake()
            params = {
                "gallery_name": gallery_name,
                "offset": offset,
                "limit": limit
            }
            async with self._session.get(
                f"http://{self._host}/gallery",
                params=params
            ) as response:
                if response.status == 200:
                    text_response = await response.text()
                    try:
                        return json.loads(text_response)
                    except json.JSONDecodeError as err:
                        _LOGGER.error("Failed to parse gallery images response: %s", err)
                return {"data": []}
        except Exception as err:
            _LOGGER.error("Error getting gallery images: %s", err)
            return {"data": []}

    async def upload_images_multi(
        self,
        images: list[tuple[str, bytes]],
        *,
        gallery: str = "default",
        override: bool = False,
        timeout: int = 60,
    ) -> bool:
        """Upload multiple images in a single request via /image/uploadMulti.

        API docs:
            POST /image/uploadMulti
            Query: gallery (optional), override: 0/1
            Body (multipart): repeated parts named "images" (array of files)

        Args:
            images: List of (filename, image_bytes) tuples.
            gallery: Destination gallery (default: "default").
            override: Overwrite existing files with same name.
            timeout: Request timeout in seconds.
        """
        if not images:
            _LOGGER.warning("upload_images_multi called with empty images list")
            return False

        try:
            await self.async_ensure_awake()
            form = aiohttp.FormData()
            for filename, image_data in images:
                form.add_field(
                    "images",
                    image_data,
                    filename=filename,
                    content_type="image/jpeg",
                )

            async with async_timeout.timeout(timeout):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_UPLOAD_MULTI}",
                    params={
                        "gallery": gallery,
                        "override": 1 if override else 0,
                    },
                    data=form,
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Multi upload failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in upload_images_multi: %s", err)
            return False

    async def upload_dithered_image_data(
        self,
        dithered_image_data: bytes,
        filename: str,
        *,
        timeout: int = 60,
    ) -> bool:
        """Upload pre-processed dithered image data via /image/dataUpload.

        API docs:
            POST /image/dataUpload
            Query: filename (required)
            Body (multipart): dithered_image (file)
        """
        try:
            await self.async_ensure_awake()
            form = aiohttp.FormData()
            form.add_field(
                "dithered_image",
                dithered_image_data,
                filename=filename,
                content_type="application/octet-stream",
            )

            async with async_timeout.timeout(timeout):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_DATA_UPLOAD}",
                    params={"filename": filename},
                    data=form,
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Dithered data upload failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in upload_dithered_image_data: %s", err)
            return False

    async def delete_image(
        self,
        filename: str,
        *,
        gallery: str = "default",
        timeout: int = 30,
    ) -> bool:
        """Delete an image from a gallery via /image/delete.

        API docs:
            POST /image/delete
            Query: image (required), gallery (optional, default: default)
        """
        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_DELETE_IMAGE}",
                    params={"image": filename, "gallery": gallery},
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Delete image failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in delete_image: %s", err)
            return False

    async def create_gallery(self, name: str, *, timeout: int = 30) -> bool:
        """Create an empty gallery via PUT /gallery.

        API docs:
            PUT /gallery
            Query: name (required)
        """
        name = self._sanitize_gallery(name)
        if not name:
            _LOGGER.error("create_gallery called without name")
            return False

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.put(
                    f"http://{self._host}{ENDPOINT_GALLERY}",
                    params={"name": name},
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Create gallery failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in create_gallery: %s", err)
            return False

    async def delete_gallery(self, name: str, *, timeout: int = 30) -> bool:
        """Delete a gallery via DELETE /gallery.

        API docs:
            DELETE /gallery
            Query: name (required)
        """
        name = self._sanitize_gallery(name)
        if not name:
            _LOGGER.error("delete_gallery called without name")
            return False

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.delete(
                    f"http://{self._host}{ENDPOINT_GALLERY}",
                    params={"name": name},
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Delete gallery failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in delete_gallery: %s", err)
            return False

    async def get_playlists(self) -> list[dict[str, Any]]:
        """List all playlists via GET /playlist/list.

        Returns:
            List like [{"name": "daily_show", "time": 1739095496}, ...]
        """
        try:
            await self.async_ensure_awake()
            async with self._session.get(f"http://{self._host}{ENDPOINT_PLAYLIST_LIST}") as response:
                if response.status == 200:
                    text_response = await response.text()
                    try:
                        return json.loads(text_response)
                    except json.JSONDecodeError as err:
                        _LOGGER.error("Failed to parse playlists response: %s", err)
                return []
        except Exception as err:
            _LOGGER.error("Error getting playlists: %s", err)
            return []

    async def get_playlist(self, name: str, *, timeout: int = 30) -> dict[str, Any] | None:
        """Get a playlist via GET /playlist.

        API docs:
            GET /playlist
            Query: name (required)
        """
        if not name:
            _LOGGER.error("get_playlist called without name")
            return None

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_PLAYLIST}",
                    params={"name": name},
                ) as response:
                    if response.status == 200:
                        text_response = await response.text()
                        try:
                            return json.loads(text_response)
                        except json.JSONDecodeError as err:
                            _LOGGER.error("Failed to parse playlist response: %s", err)
                            return None
                    response_text = await response.text()
                    _LOGGER.error(
                        "Get playlist failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return None
        except Exception as err:
            _LOGGER.error("Error in get_playlist: %s", err)
            return None

    async def put_playlist(self, playlist_payload: dict[str, Any], *, timeout: int = 30) -> bool:
        """Create/overwrite a playlist via PUT /playlist."""
        if not playlist_payload:
            _LOGGER.error("put_playlist called with empty payload")
            return False

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.put(
                    f"http://{self._host}{ENDPOINT_PLAYLIST}",
                    json=playlist_payload,
                    headers={"Content-Type": "application/json"},
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Put playlist failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in put_playlist: %s", err)
            return False

    async def delete_playlist(self, name: str, *, timeout: int = 30) -> bool:
        """Delete a playlist via DELETE /playlist."""
        if not name:
            _LOGGER.error("delete_playlist called without name")
            return False

        try:
            await self.async_ensure_awake()
            async with async_timeout.timeout(timeout):
                async with self._session.delete(
                    f"http://{self._host}{ENDPOINT_PLAYLIST}",
                    params={"name": name},
                ) as response:
                    if response.status == 200:
                        return True
                    response_text = await response.text()
                    _LOGGER.error(
                        "Delete playlist failed: %s - %s",
                        response.status,
                        response_text,
                    )
                    return False
        except Exception as err:
            _LOGGER.error("Error in delete_playlist: %s", err)
            return False
