"""The BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass, field
from io import BytesIO
import logging
import re
import unicodedata
from urllib.parse import urlparse
import voluptuous as vol
from datetime import datetime, timedelta
from typing import Any, Callable, TypeAlias

from PIL import Image
import async_timeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_send, dispatcher_send
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.exceptions import ServiceValidationError

from .coordinator import EinkCanvasDeviceInfoCoordinator

from .api_client import EinkCanvasApiClient
from .const import (
    DOMAIN,
    DEFAULT_NAME,
    SIGNAL_DEVICE_INFO_UPDATED,
    CONF_MAC_ADDRESS,
    CONF_BLE_AUTO_WAKE,
    DEFAULT_BLE_AUTO_WAKE,
    CONF_ORIENTATION,
    CONF_FILL_MODE,
    CONF_CONTAIN_COLOR,
    DEFAULT_ORIENTATION,
    DEFAULT_FILL_MODE,
    DEFAULT_CONTAIN_COLOR,
    ORIENTATION_LANDSCAPE,
    FILL_MODE_AUTO,
    FILL_MODE_COVER,
    FILL_MODE_CONTAIN,
    ATTR_DEVICE_ID,
    ATTR_ENTITY_ID,
    SERVICE_NAMES,
)

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


try:
    # Available in newer Home Assistant versions; allows returning data to the caller.
    from homeassistant.core import SupportsResponse  # type: ignore
except Exception:  # pragma: no cover
    SupportsResponse = None  # type: ignore


@dataclass
class RuntimeData:
    """Runtime data for BLOOMIN8 E-Ink Canvas integration."""

    api_client: EinkCanvasApiClient
    coordinator: EinkCanvasDeviceInfoCoordinator
    device_info: dict[str, Any] | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    unsub_coordinator_listener: Callable[[], None] | None = None


# Extend ConfigEntry to type hint runtime_data
EinkCanvasConfigEntry: TypeAlias = ConfigEntry


# Supported platforms
PLATFORMS: list[Platform] = [
    Platform.MEDIA_PLAYER,
    Platform.SENSOR,
    Platform.BUTTON,
    Platform.SELECT,
    Platform.TEXT,
]


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the BLOOMIN8 E-Ink Canvas component."""
    return True


async def async_setup_entry(hass: HomeAssistant, entry: EinkCanvasConfigEntry) -> bool:
    """Set up BLOOMIN8 E-Ink Canvas from a config entry."""
    host = entry.data[CONF_HOST]
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)

    mac_address = (entry.data.get(CONF_MAC_ADDRESS) or "").strip()
    ble_auto_wake = bool(entry.data.get(CONF_BLE_AUTO_WAKE, DEFAULT_BLE_AUTO_WAKE))

    # Create API client
    api_client = EinkCanvasApiClient(
        hass,
        host,
        mac_address=mac_address,
        ble_auto_wake=ble_auto_wake,
    )

    # Centralize device-info fetching (no polling — push-only for battery savings).
    coordinator = EinkCanvasDeviceInfoCoordinator(
        hass,
        api_client=api_client,
        entry_id=entry.entry_id,
    )

    # Load cached data from disk first (survives HA restarts).
    await coordinator.async_load_cached_data()

    # Try to fetch fresh data (non-blocking; doesn't fail setup if device is asleep).
    await coordinator.async_refresh()

    # Store runtime data
    runtime_data = RuntimeData(api_client=api_client, coordinator=coordinator)
    entry.runtime_data = runtime_data

    # Keep runtime_data.device_info in sync with coordinator snapshots.
    # This allows non-coordinator entities (select/text) to update from the same cache.
    signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"

    @callback
    def _on_coordinator_update() -> None:
        if coordinator.data is not None:
            runtime_data.device_info = coordinator.data
            async_dispatcher_send(hass, signal)

    runtime_data.unsub_coordinator_listener = coordinator.async_add_listener(_on_coordinator_update)

    # Store runtime data in hass.data for multi-device service lookup
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = runtime_data

    # Create device registration
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, host)},
        name=name,
        manufacturer="BLOOMIN8",
        model="E-Ink Canvas",
    )

    # Register services (only once, on first entry)
    if not hass.services.has_service(DOMAIN, "sleep"):
        _register_services(hass)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


# ---------------------------------------------------------------------------
# Multi-device service infrastructure
# ---------------------------------------------------------------------------


def _get_entries_from_service_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> list[EinkCanvasConfigEntry]:
    """Resolve service call targets to config entries.

    If no target is specified (entity_id/device_id), returns all loaded entries
    (backwards-compatible behavior) and logs a warning.
    """
    entity_ids: list[str] | None = call.data.get(ATTR_ENTITY_ID)
    device_ids: list[str] | None = call.data.get(ATTR_DEVICE_ID)

    # Normalize to lists
    if entity_ids is not None and not isinstance(entity_ids, list):
        entity_ids = [entity_ids]
    if device_ids is not None and not isinstance(device_ids, list):
        device_ids = [device_ids]

    # No target specified -> all entries (backwards-compatible)
    if not entity_ids and not device_ids:
        all_entries = [
            e
            for e in hass.config_entries.async_entries(DOMAIN)
            if e.entry_id in hass.data.get(DOMAIN, {})
        ]
        if len(all_entries) > 1:
            _LOGGER.warning(
                "No target specified for service %s; applying to all %d devices",
                call.service,
                len(all_entries),
            )
        return all_entries

    entry_ids: set[str] = set()

    if entity_ids:
        entity_registry = er.async_get(hass)
        for entity_id in entity_ids:
            if entity := entity_registry.async_get(entity_id):
                if entity.config_entry_id:
                    entry_ids.add(entity.config_entry_id)

    if device_ids:
        device_registry = dr.async_get(hass)
        for device_id in device_ids:
            if device := device_registry.async_get(device_id):
                for eid in device.config_entries:
                    entry_ids.add(eid)

    entries: list[EinkCanvasConfigEntry] = []
    domain_data = hass.data.get(DOMAIN, {})
    for entry_id in entry_ids:
        if entry_id in domain_data:
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry:
                entries.append(entry)

    return entries


def _get_single_entry_from_service_call(
    hass: HomeAssistant,
    call: ServiceCall,
) -> EinkCanvasConfigEntry:
    """Get exactly one entry from service call (for response-based services).

    Raises ServiceValidationError if zero or multiple entries are targeted.
    """
    entries = _get_entries_from_service_call(hass, call)
    if len(entries) == 0:
        raise ServiceValidationError(
            f"No target device found for service {call.service}",
            translation_domain=DOMAIN,
            translation_key="no_target_device",
        )
    if len(entries) > 1:
        raise ServiceValidationError(
            f"Service {call.service} requires exactly one target device, got {len(entries)}",
            translation_domain=DOMAIN,
            translation_key="multiple_targets_not_supported",
        )
    return entries[0]


def _get_runtime_data(hass: HomeAssistant, entry: EinkCanvasConfigEntry) -> RuntimeData:
    """Get runtime data for an entry from hass.data."""
    return hass.data[DOMAIN][entry.entry_id]


# ---------------------------------------------------------------------------
# Stateless helper functions (moved out of closure)
# ---------------------------------------------------------------------------


def _derive_filename_from_url(url: str) -> str:
    """Derive a safe-ish filename from a URL."""
    parsed = urlparse(url)
    name = (parsed.path or "").split("/")[-1]
    if not name:
        name = f"ha_{int(datetime.now().timestamp() * 1000)}.jpg"
    if "." not in name:
        name = f"{name}.jpg"
    return name


def _sanitize_filename(name: str) -> str:
    """Make a device-safe filename (ASCII-ish, no spaces, enforce .jpg)."""
    original = (name or "").strip()
    normalized = unicodedata.normalize("NFKD", original).encode("ascii", "ignore").decode("ascii")
    normalized = normalized.replace("/", "_").replace("\\", "_")
    normalized = re.sub(r"\s+", "_", normalized)
    normalized = re.sub(r"[^A-Za-z0-9._-]", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("._-")

    if not normalized:
        normalized = f"ha_{int(datetime.now().timestamp() * 1000)}"

    lower = normalized.lower()
    if lower.endswith(".jpeg"):
        normalized = normalized[:-5] + ".jpg"
    elif not lower.endswith(".jpg"):
        if "." in normalized:
            normalized = normalized.rsplit(".", 1)[0]
        normalized = f"{normalized}.jpg"

    if len(normalized) > 80:
        stem, ext = normalized.rsplit(".", 1)
        normalized = f"{stem[:75]}.{ext}"

    return normalized


def _looks_like_url(text: str) -> bool:
    """Check if text looks like a URL."""
    t = (text or "").strip()
    return t.startswith("http://") or t.startswith("https://")


def _decode_base64_image_bytes(value: Any) -> bytes:
    """Decode base64 that may include whitespace, missing padding, urlsafe alphabet or a data: URI."""
    text = ("" if value is None else str(value)).strip()
    if not text:
        raise ValueError("empty image_data")

    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]

    text = "".join(text.split())

    pad = (-len(text)) % 4
    if pad:
        text = text + ("=" * pad)

    try:
        return base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError):
        try:
            return base64.urlsafe_b64decode(text)
        except (binascii.Error, ValueError) as err:
            raise ValueError(f"invalid base64: {err}") from err


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert hex color string to RGB tuple."""
    hex_color = (hex_color or "").lstrip("#")
    if len(hex_color) != 6:
        return (255, 255, 255)
    try:
        return (
            int(hex_color[0:2], 16),
            int(hex_color[2:4], 16),
            int(hex_color[4:6], 16),
        )
    except ValueError:
        return (255, 255, 255)


def _convert_to_rgb(image: Image.Image) -> Image.Image:
    """Convert image to RGB, flattening alpha onto white background."""
    if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
        background = Image.new("RGB", image.size, (255, 255, 255))
        if image.mode == "P":
            image = image.convert("RGBA")
        background.paste(image, mask=image.split()[-1])
        return background
    if image.mode != "RGB":
        return image.convert("RGB")
    return image


def _cover_image(image: Image.Image, target_width: int, target_height: int) -> Image.Image:
    """Scale and crop image to cover the target area (center crop)."""
    image_aspect = image.width / image.height
    target_aspect = target_width / target_height

    if image_aspect > target_aspect:
        scaled_height = target_height
        scaled_width = int(target_height * image_aspect)
    else:
        scaled_width = target_width
        scaled_height = int(target_width / image_aspect)

    scaled = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
    x_offset = (scaled_width - target_width) // 2
    y_offset = (scaled_height - target_height) // 2
    return scaled.crop((x_offset, y_offset, x_offset + target_width, y_offset + target_height))


def _contain_image(
    image: Image.Image,
    target_width: int,
    target_height: int,
    bg_color: tuple[int, int, int],
) -> Image.Image:
    """Scale image to fit within target area, fill remaining with background color."""
    image_aspect = image.width / image.height
    target_aspect = target_width / target_height

    if image_aspect > target_aspect:
        scaled_width = target_width
        scaled_height = int(target_width / image_aspect)
    else:
        scaled_height = target_height
        scaled_width = int(target_height * image_aspect)

    scaled = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)
    background = Image.new("RGB", (target_width, target_height), bg_color)
    x_offset = (target_width - scaled_width) // 2
    y_offset = (target_height - scaled_height) // 2
    background.paste(scaled, (x_offset, y_offset))
    return background


def _process_with_orientation(
    image: Image.Image,
    screen_width: int,
    screen_height: int,
    orientation: str,
    fill_mode: str,
    contain_color: str,
) -> Image.Image:
    """Resize image to device resolution using same orientation/fill rules as media_player."""
    canvas_is_landscape = orientation == ORIENTATION_LANDSCAPE
    if canvas_is_landscape:
        target_width = screen_height
        target_height = screen_width
    else:
        target_width = screen_width
        target_height = screen_height

    image_is_landscape = image.width > image.height
    if fill_mode == FILL_MODE_AUTO:
        actual_fill_mode = FILL_MODE_COVER if (image_is_landscape == canvas_is_landscape) else FILL_MODE_CONTAIN
    else:
        actual_fill_mode = fill_mode

    if actual_fill_mode == FILL_MODE_COVER:
        processed = _cover_image(image, target_width, target_height)
    else:
        processed = _contain_image(image, target_width, target_height, _hex_to_rgb(contain_color))

    if canvas_is_landscape:
        processed = processed.rotate(-90, expand=True)

    return processed


def _snap_to_supported_sizes(width: int, height: int) -> tuple[int, int]:
    """Snap (width,height) to known panel sizes."""
    w = int(width or 0)
    h = int(height or 0)
    if w <= 0 or h <= 0:
        return 1200, 1600
    if max(w, h) <= 900:
        return 480, 800
    return 1200, 1600


# ---------------------------------------------------------------------------
# Service registration
# ---------------------------------------------------------------------------


def _register_services(hass: HomeAssistant) -> None:
    """Register device control services (called once, not per-entry)."""

    # ---------------------------------------------------------------------------
    # Per-entry helper functions
    # ---------------------------------------------------------------------------

    def add_log(
        hass: HomeAssistant,
        entry: EinkCanvasConfigEntry,
        message: str,
        level: str = "info",
    ) -> None:
        """Add log entry for a specific entry."""
        runtime_data = _get_runtime_data(hass, entry)
        log_entry = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        }
        runtime_data.logs.append(log_entry)
        if len(runtime_data.logs) > 50:
            runtime_data.logs.pop(0)
        signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
        dispatcher_send(hass, signal)

    async def _download_bytes(hass: HomeAssistant, url: str) -> bytes:
        """Download bytes from a URL."""
        session = async_get_clientsession(hass)
        async with async_timeout.timeout(30):
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    raise ValueError(f"download failed ({resp.status})")
                return await resp.read()

    async def _resolve_raw_image_bytes_from_service_field(hass: HomeAssistant, value: Any) -> bytes:
        """Resolve raw image bytes from service field."""
        if isinstance(value, str) and _looks_like_url(value):
            return await _download_bytes(hass, value)

        raw = _decode_base64_image_bytes(value)

        try:
            as_text = raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return raw

        if _looks_like_url(as_text):
            return await _download_bytes(hass, as_text)

        return raw

    async def _get_screen_resolution(
        hass: HomeAssistant,
        entry: EinkCanvasConfigEntry,
    ) -> tuple[int, int]:
        """Get screen resolution from device info, with safe fallback."""
        runtime_data = _get_runtime_data(hass, entry)
        api_client = runtime_data.api_client
        device_info = runtime_data.device_info

        if not device_info:
            device_info = await api_client.get_device_info(wake=True)

        if device_info:
            if runtime_data.device_info is not device_info:
                runtime_data.device_info = device_info
                signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
                dispatcher_send(hass, signal)
            width = int(device_info.get("width", 1200) or 1200)
            height = int(device_info.get("height", 1600) or 1600)
            return _snap_to_supported_sizes(width, height)
        return _snap_to_supported_sizes(1200, 1600)

    async def _to_jpeg_bytes(
        hass: HomeAssistant,
        entry: EinkCanvasConfigEntry,
        raw: bytes,
        *,
        process: bool,
        screen_width: int,
        screen_height: int,
    ) -> bytes:
        """Convert incoming bytes to JPEG (optionally resize/crop)."""
        orientation = entry.data.get(CONF_ORIENTATION, DEFAULT_ORIENTATION)
        fill_mode = entry.data.get(CONF_FILL_MODE, DEFAULT_FILL_MODE)
        contain_color = entry.data.get(CONF_CONTAIN_COLOR, DEFAULT_CONTAIN_COLOR)

        def _work() -> bytes:
            try:
                image = Image.open(BytesIO(raw))
            except Exception as err:
                head = raw[:32]
                head_hex = head.hex()
                raise ValueError(
                    f"Input is not a supported image (len={len(raw)}, head_hex={head_hex}). "
                    "Provide base64 of an actual PNG/JPEG/WebP/etc., or use upload_dithered_image_data for raw data."
                ) from err
            image = _convert_to_rgb(image)
            if process:
                image = _process_with_orientation(
                    image,
                    screen_width,
                    screen_height,
                    orientation,
                    fill_mode,
                    contain_color,
                )
            out = BytesIO()
            image.save(out, format="JPEG", quality=95)
            return out.getvalue()

        return await hass.async_add_executor_job(_work)

    # ---------------------------------------------------------------------------
    # Service handlers (iterate over targeted entries)
    # ---------------------------------------------------------------------------

    async def handle_show_next(call: ServiceCall) -> None:
        """Handle show next image service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.show_next()
            if success:
                add_log(hass, entry, "Successfully switched to next image")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, "Failed to switch to next image", "error")

    async def handle_sleep(call: ServiceCall) -> None:
        """Handle device sleep service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.sleep()
            if success:
                add_log(hass, entry, "Device entered sleep mode")
            else:
                add_log(hass, entry, "Device sleep failed", "error")

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle device reboot service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.reboot()
            if success:
                add_log(hass, entry, "Device reboot command sent")
            else:
                add_log(hass, entry, "Device reboot failed", "error")

    async def handle_clear_screen(call: ServiceCall) -> None:
        """Handle clear screen service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.clear_screen()
            if success:
                add_log(hass, entry, "Screen cleared")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, "Clear screen failed", "error")

    async def handle_whistle(call: ServiceCall) -> None:
        """Handle keep alive service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.whistle()
            if success:
                add_log(hass, entry, "Keep alive signal sent")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, "Keep alive failed", "error")

    async def handle_update_settings(call: ServiceCall) -> None:
        """Handle update device settings service."""
        entries = _get_entries_from_service_call(hass, call)
        settings_data = {}

        if "name" in call.data:
            settings_data["name"] = call.data["name"]
        if "sleep_duration" in call.data:
            settings_data["sleep_duration"] = call.data["sleep_duration"]
        if "max_idle" in call.data:
            settings_data["max_idle"] = call.data["max_idle"]
        if "idx_wake_sens" in call.data:
            settings_data["idx_wake_sens"] = call.data["idx_wake_sens"]

        if not settings_data:
            for entry in entries:
                add_log(hass, entry, "No settings parameters provided", "warning")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.update_settings(settings_data)
            if success:
                settings_str = ", ".join([f"{k}: {v}" for k, v in settings_data.items()])
                add_log(hass, entry, f"Device settings updated: {settings_str}")
                cached = runtime_data.device_info or {}
                updated = {**cached, **settings_data}
                runtime_data.device_info = updated
                runtime_data.coordinator.async_set_updated_data(updated)
                signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
                dispatcher_send(hass, signal)
            else:
                add_log(hass, entry, "Settings update failed", "error")

    async def handle_refresh_device_info(call: ServiceCall) -> None:
        """Handle refresh device info service."""
        entries = _get_entries_from_service_call(hass, call)
        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            device_info = await runtime_data.api_client.get_device_info(wake=True)
            if device_info:
                runtime_data.device_info = device_info
                add_log(hass, entry, "Device info refreshed")
                runtime_data.coordinator.async_set_updated_data(device_info)
                signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
                dispatcher_send(hass, signal)
            else:
                add_log(hass, entry, "Failed to refresh device info", "error")

    async def handle_upload_image_url(call: ServiceCall) -> None:
        """Download an image from a URL, upload to the device, optionally show immediately."""
        entries = _get_entries_from_service_call(hass, call)
        url = call.data["url"]
        gallery = call.data.get("gallery", "default")
        filename = call.data.get("filename") or _derive_filename_from_url(url)
        safe_filename = _sanitize_filename(filename)
        show_now = bool(call.data.get("show_now", True))
        process = bool(call.data.get("process", True))

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            if safe_filename != filename:
                add_log(hass, entry, f"Filename sanitized: '{filename}' -> '{safe_filename}'", "warning")
            try:
                raw = await _download_bytes(hass, url)
                screen_w, screen_h = await _get_screen_resolution(hass, entry)
                jpeg = await _to_jpeg_bytes(hass, entry, raw, process=process, screen_width=screen_w, screen_height=screen_h)
                uploaded_path = await runtime_data.api_client.upload_image(
                    jpeg,
                    safe_filename,
                    gallery=gallery,
                    show_now=show_now,
                )
                if uploaded_path:
                    add_log(hass, entry, f"Uploaded image from URL to {uploaded_path}")
                    device_info = await runtime_data.api_client.get_device_info(wake=True)
                    if device_info:
                        runtime_data.device_info = device_info
                        runtime_data.coordinator.async_set_updated_data(device_info)
                        signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
                        dispatcher_send(hass, signal)
                else:
                    add_log(hass, entry, f"Upload failed for URL image: {url}", "error")
            except Exception as err:
                _LOGGER.exception("Error in upload_image_url service: %s", err)
                add_log(hass, entry, f"Upload image URL failed: {err}", "error")

    async def handle_upload_images_multi(call: ServiceCall) -> None:
        """Upload multiple images in one request via /image/uploadMulti."""
        entries = _get_entries_from_service_call(hass, call)
        gallery = call.data.get("gallery", "default")
        override = bool(call.data.get("override", False))
        process = bool(call.data.get("process", True))
        images_in = call.data.get("images")

        if not images_in:
            for entry in entries:
                add_log(hass, entry, "Missing images for upload_images_multi", "error")
            return

        if isinstance(images_in, dict):
            images_list: list[Any] = [images_in]
        elif isinstance(images_in, list):
            images_list = images_in
        else:
            for entry in entries:
                add_log(hass, entry, "Field 'images' must be a list or an object", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            screen_w, screen_h = await _get_screen_resolution(hass, entry)
            prepared: list[tuple[str, bytes]] = []
            skipped = 0

            for idx, item in enumerate(images_list):
                try:
                    if not isinstance(item, dict):
                        skipped += 1
                        add_log(hass, entry, f"upload_images_multi: item #{idx} is not an object", "warning")
                        continue

                    item_filename = item.get("filename")
                    item_url = item.get("url")
                    image_data_b64 = item.get("image_data")

                    if not item_filename:
                        if item_url:
                            item_filename = _derive_filename_from_url(str(item_url))
                        else:
                            item_filename = f"ha_{int(datetime.now().timestamp() * 1000)}_{idx}.jpg"

                    safe_item_filename = _sanitize_filename(str(item_filename))
                    if safe_item_filename != item_filename:
                        add_log(hass, entry, f"upload_images_multi: filename sanitized: '{item_filename}' -> '{safe_item_filename}'", "warning")

                    if item_url:
                        raw = await _download_bytes(hass, str(item_url))
                    elif image_data_b64:
                        normalized = "".join(str(image_data_b64).split())
                        raw = base64.b64decode(normalized, validate=True)
                    else:
                        skipped += 1
                        add_log(hass, entry, f"upload_images_multi: item #{idx} missing 'url' or 'image_data'", "warning")
                        continue

                    jpeg = await _to_jpeg_bytes(hass, entry, raw, process=process, screen_width=screen_w, screen_height=screen_h)
                    prepared.append((safe_item_filename, jpeg))
                except Exception as err:
                    skipped += 1
                    _LOGGER.exception("upload_images_multi: failed to prepare item #%s: %s", idx, err)
                    add_log(hass, entry, f"upload_images_multi: failed to prepare item #{idx}: {err}", "warning")

            if not prepared:
                add_log(hass, entry, "upload_images_multi: no valid images to upload", "error")
                continue

            success = await runtime_data.api_client.upload_images_multi(prepared, gallery=gallery, override=override)
            if success:
                add_log(hass, entry, f"Uploaded {len(prepared)} images via uploadMulti to gallery '{gallery}' (skipped={skipped})")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, f"upload_images_multi failed for gallery '{gallery}' (prepared={len(prepared)}, skipped={skipped})", "error")

    async def handle_list_playlists(call: ServiceCall) -> dict[str, Any] | None:
        """List playlists via GET /playlist/list."""
        entry = _get_single_entry_from_service_call(hass, call)
        runtime_data = _get_runtime_data(hass, entry)
        playlists = await runtime_data.api_client.get_playlists()
        add_log(hass, entry, f"Playlists: {len(playlists)}")
        if SupportsResponse is not None:
            return {"playlists": playlists}
        return None

    async def handle_get_playlist(call: ServiceCall) -> dict[str, Any] | None:
        """Get playlist via GET /playlist?name=..."""
        entry = _get_single_entry_from_service_call(hass, call)
        runtime_data = _get_runtime_data(hass, entry)
        name = call.data.get("name")
        if not name:
            add_log(hass, entry, "Missing name for get_playlist", "error")
            return {"error": "missing name"} if SupportsResponse is not None else None
        playlist = await runtime_data.api_client.get_playlist(str(name))
        if playlist is None:
            add_log(hass, entry, f"Get playlist failed: {name}", "error")
            if SupportsResponse is not None:
                return {"name": str(name), "playlist": None}
            return None
        add_log(hass, entry, f"Fetched playlist: {name}")
        if SupportsResponse is not None:
            return {"name": str(name), "playlist": playlist}
        return None

    async def handle_list_galleries(call: ServiceCall) -> dict[str, Any] | None:
        """List galleries via GET /gallery/list."""
        entry = _get_single_entry_from_service_call(hass, call)
        runtime_data = _get_runtime_data(hass, entry)
        galleries = await runtime_data.api_client.get_galleries()
        add_log(hass, entry, f"Galleries: {len(galleries)}")
        if SupportsResponse is not None:
            return {"galleries": galleries}
        return None

    async def handle_upload_image_data(call: ServiceCall) -> None:
        """Upload an image provided as base64-encoded bytes."""
        entries = _get_entries_from_service_call(hass, call)
        image_data_b64 = call.data["image_data"]
        gallery = call.data.get("gallery", "default")
        filename = call.data.get("filename") or f"ha_{int(datetime.now().timestamp() * 1000)}.jpg"
        safe_filename = _sanitize_filename(filename)
        show_now = bool(call.data.get("show_now", True))
        process = bool(call.data.get("process", True))

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            if safe_filename != filename:
                add_log(hass, entry, f"Filename sanitized: '{filename}' -> '{safe_filename}'", "warning")

            try:
                raw = await _resolve_raw_image_bytes_from_service_field(hass, image_data_b64)
            except Exception as err:
                add_log(hass, entry, f"Invalid image_data (expected base64 or URL): {err}", "error")
                continue

            try:
                screen_w, screen_h = await _get_screen_resolution(hass, entry)
                jpeg = await _to_jpeg_bytes(hass, entry, raw, process=process, screen_width=screen_w, screen_height=screen_h)
                uploaded_path = await runtime_data.api_client.upload_image(
                    jpeg,
                    safe_filename,
                    gallery=gallery,
                    show_now=show_now,
                )
                if uploaded_path:
                    add_log(hass, entry, f"Uploaded image data to {uploaded_path}")
                    device_info = await runtime_data.api_client.get_device_info(wake=True)
                    if device_info:
                        runtime_data.device_info = device_info
                        runtime_data.coordinator.async_set_updated_data(device_info)
                        signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"
                        dispatcher_send(hass, signal)
                else:
                    add_log(hass, entry, f"Upload failed for image data: {safe_filename}", "error")
            except Exception as err:
                _LOGGER.exception("Error in upload_image_data service: %s", err)
                add_log(hass, entry, f"Upload image data failed: {err}", "error")

    async def handle_upload_dithered_image_data(call: ServiceCall) -> None:
        """Upload dithered raw image data (advanced)."""
        entries = _get_entries_from_service_call(hass, call)
        dithered_b64 = call.data["dithered_image_data"]
        filename = call.data["filename"]

        try:
            normalized = "".join(str(dithered_b64).split())
            raw = base64.b64decode(normalized, validate=True)
        except Exception:
            for entry in entries:
                add_log(hass, entry, "Invalid base64 in dithered_image_data", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.upload_dithered_image_data(raw, filename)
            if success:
                add_log(hass, entry, f"Uploaded dithered image data: {filename}")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, f"Dithered image data upload failed: {filename}", "error")

    async def handle_delete_image(call: ServiceCall) -> None:
        """Delete an image from a gallery."""
        entries = _get_entries_from_service_call(hass, call)
        filename = call.data.get("filename") or call.data.get("image")
        gallery = call.data.get("gallery", "default")

        if not filename:
            for entry in entries:
                add_log(hass, entry, "Missing filename for delete_image", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.delete_image(filename, gallery=gallery)
            if success:
                add_log(hass, entry, f"Deleted image: {gallery}/{filename}")
            else:
                add_log(hass, entry, f"Delete image failed: {gallery}/{filename}", "error")

    async def handle_create_gallery(call: ServiceCall) -> None:
        """Create a gallery."""
        entries = _get_entries_from_service_call(hass, call)
        name = call.data.get("name")

        if not name:
            for entry in entries:
                add_log(hass, entry, "Missing name for create_gallery", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.create_gallery(name)
            if success:
                add_log(hass, entry, f"Created gallery: {name}")
            else:
                add_log(hass, entry, f"Create gallery failed: {name}", "error")

    async def handle_delete_gallery(call: ServiceCall) -> None:
        """Delete a gallery."""
        entries = _get_entries_from_service_call(hass, call)
        name = call.data.get("name")

        if not name:
            for entry in entries:
                add_log(hass, entry, "Missing name for delete_gallery", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.delete_gallery(name)
            if success:
                add_log(hass, entry, f"Deleted gallery: {name}")
            else:
                add_log(hass, entry, f"Delete gallery failed: {name}", "error")

    async def handle_show_playlist(call: ServiceCall) -> None:
        """Start playlist playback."""
        entries = _get_entries_from_service_call(hass, call)
        playlist = call.data.get("playlist")
        image = call.data.get("image")
        dither = call.data.get("dither")

        if not playlist:
            for entry in entries:
                add_log(hass, entry, "Missing playlist for show_playlist", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.show_playlist(playlist, image=image, dither=dither)
            if success:
                add_log(hass, entry, f"Started playlist: {playlist}")
                try:
                    await runtime_data.coordinator.async_request_refresh()
                except Exception:
                    _LOGGER.debug("Post-action refresh failed (device may be asleep)")
            else:
                add_log(hass, entry, f"Start playlist failed: {playlist}", "error")

    async def handle_put_playlist(call: ServiceCall) -> None:
        """Create/overwrite a playlist."""
        entries = _get_entries_from_service_call(hass, call)
        name = call.data.get("name")
        playlist_type = call.data.get("type")
        time_offset = call.data.get("time_offset")
        items = call.data.get("items")

        if not name or not playlist_type or items is None:
            for entry in entries:
                add_log(hass, entry, "Missing required fields for put_playlist (name, type, items)", "error")
            return

        payload: dict[str, Any] = {
            "name": name,
            "type": playlist_type,
            "list": items,
        }
        if time_offset is not None:
            payload["time_offset"] = time_offset

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.put_playlist(payload)
            if success:
                add_log(hass, entry, f"Playlist created/updated: {name}")
            else:
                add_log(hass, entry, f"Put playlist failed: {name}", "error")

    async def handle_delete_playlist(call: ServiceCall) -> None:
        """Delete a playlist."""
        entries = _get_entries_from_service_call(hass, call)
        name = call.data.get("name")

        if not name:
            for entry in entries:
                add_log(hass, entry, "Missing name for delete_playlist", "error")
            return

        for entry in entries:
            runtime_data = _get_runtime_data(hass, entry)
            success = await runtime_data.api_client.delete_playlist(name)
            if success:
                add_log(hass, entry, f"Deleted playlist: {name}")
            else:
                add_log(hass, entry, f"Delete playlist failed: {name}", "error")

    # ---------------------------------------------------------------------------
    # Service registration
    # ---------------------------------------------------------------------------

    services: list[tuple[str, Any, dict, Any]] = [
        ("show_next", handle_show_next, {}, None),
        ("sleep", handle_sleep, {}, None),
        ("reboot", handle_reboot, {}, None),
        ("clear_screen", handle_clear_screen, {}, None),
        ("whistle", handle_whistle, {}, None),
        ("refresh_device_info", handle_refresh_device_info, {}, None),
        (
            "upload_image_url",
            handle_upload_image_url,
            {
                vol.Required("url"): cv.url,
                vol.Optional("filename"): cv.string,
                vol.Optional("gallery", default="default"): cv.string,
                vol.Optional("show_now", default=True): cv.boolean,
                vol.Optional("process", default=True): cv.boolean,
            },
            None,
        ),
        (
            "upload_image_data",
            handle_upload_image_data,
            {
                vol.Required("image_data"): cv.string,
                vol.Optional("filename"): cv.string,
                vol.Optional("gallery", default="default"): cv.string,
                vol.Optional("show_now", default=True): cv.boolean,
                vol.Optional("process", default=True): cv.boolean,
            },
            None,
        ),
        (
            "upload_images_multi",
            handle_upload_images_multi,
            {
                vol.Required("images"): vol.Any(cv.ensure_list, dict),
                vol.Optional("gallery", default="default"): cv.string,
                vol.Optional("override", default=False): cv.boolean,
                vol.Optional("process", default=True): cv.boolean,
            },
            None,
        ),
        (
            "upload_dithered_image_data",
            handle_upload_dithered_image_data,
            {
                vol.Required("dithered_image_data"): cv.string,
                vol.Required("filename"): cv.string,
            },
            None,
        ),
        (
            "update_settings",
            handle_update_settings,
            {
                vol.Optional("name"): str,
                vol.Optional("sleep_duration"): int,
                vol.Optional("max_idle"): int,
                vol.Optional("idx_wake_sens"): int,
            },
            None,
        ),
        (
            "delete_image",
            handle_delete_image,
            {
                vol.Required("filename"): cv.string,
                vol.Optional("gallery", default="default"): cv.string,
            },
            None,
        ),
        ("create_gallery", handle_create_gallery, {vol.Required("name"): cv.string}, None),
        ("delete_gallery", handle_delete_gallery, {vol.Required("name"): cv.string}, None),
        (
            "list_galleries",
            handle_list_galleries,
            {},
            (SupportsResponse.OPTIONAL if SupportsResponse is not None else None),
        ),
        (
            "show_playlist",
            handle_show_playlist,
            {
                vol.Required("playlist"): cv.string,
                vol.Optional("image"): cv.string,
                vol.Optional("dither"): vol.Coerce(int),
            },
            None,
        ),
        (
            "list_playlists",
            handle_list_playlists,
            {},
            (SupportsResponse.OPTIONAL if SupportsResponse is not None else None),
        ),
        (
            "get_playlist",
            handle_get_playlist,
            {vol.Required("name"): cv.string},
            (SupportsResponse.OPTIONAL if SupportsResponse is not None else None),
        ),
        (
            "put_playlist",
            handle_put_playlist,
            {
                vol.Required("name"): cv.string,
                vol.Required("type"): vol.In(["duration", "time"]),
                vol.Optional("time_offset"): vol.Coerce(int),
                vol.Required("items"): cv.ensure_list,
            },
            None,
        ),
        ("delete_playlist", handle_delete_playlist, {vol.Required("name"): cv.string}, None),
    ]

    for service_name, handler, schema, supports_response in services:
        kwargs: dict[str, Any] = {"schema": vol.Schema(schema)}
        if supports_response is not None:
            kwargs["supports_response"] = supports_response

        hass.services.async_register(
            DOMAIN,
            service_name,
            handler,
            **kwargs,
        )


async def async_unload_entry(hass: HomeAssistant, entry: EinkCanvasConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Detach coordinator listener (if any).
        runtime_data = getattr(entry, "runtime_data", None)
        if runtime_data is not None and getattr(runtime_data, "unsub_coordinator_listener", None):
            try:
                runtime_data.unsub_coordinator_listener()  # type: ignore[misc]
            except Exception:
                _LOGGER.debug("Failed to unsubscribe coordinator listener", exc_info=True)

        # Remove this entry from hass.data
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            hass.data[DOMAIN].pop(entry.entry_id)

        # Only remove services when the last entry is unloaded
        if not hass.data.get(DOMAIN):
            for service in SERVICE_NAMES:
                if hass.services.has_service(DOMAIN, service):
                    hass.services.async_remove(DOMAIN, service)
            hass.data.pop(DOMAIN, None)

    return unload_ok
