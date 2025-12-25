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
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.dispatcher import async_dispatcher_send, dispatcher_send
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .coordinator import (
    EinkCanvasDeviceInfoCoordinator,
    compute_safe_poll_interval_seconds,
)

from .api_client import EinkCanvasApiClient
from .const import (
    DOMAIN,
    DEFAULT_NAME,
    SIGNAL_DEVICE_INFO_UPDATED,
    CONF_ENABLE_POLLING,
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

    # Centralize device-info fetching (avoids one HTTP call per entity).
    enable_polling = bool(entry.data.get(CONF_ENABLE_POLLING, False))

    # "enable_polling" is intentionally implemented as *safe polling*:
    # we only poll at an interval strictly larger than the device's max_idle
    # so polling should not keep the device awake.
    polling_interval = None
    if enable_polling:
        # We do not know max_idle yet at startup; use a safe fallback (> default max_idle).
        safe_seconds = compute_safe_poll_interval_seconds(None)
        polling_interval = timedelta(seconds=safe_seconds)
    coordinator = EinkCanvasDeviceInfoCoordinator(
        hass,
        api_client=api_client,
        update_interval=polling_interval,
        safe_polling=enable_polling,
    )
    # Do not fail setup if the device is asleep/offline.
    # We prefer a loaded integration with unavailable entities.
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

    # Create device registration
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, host)},
        name=name,
        manufacturer="BLOOMIN8",
        model="E-Ink Canvas",
    )

    # Register services
    await _register_services(hass, entry)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def _register_services(hass: HomeAssistant, entry: EinkCanvasConfigEntry) -> None:
    """Register device control services."""
    runtime_data = entry.runtime_data
    api_client = runtime_data.api_client
    coordinator = runtime_data.coordinator

    signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry.entry_id}"

    def add_log(message: str, level: str = "info") -> None:
        """Add log entry (synchronous)."""
        log_entry = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        }

        runtime_data.logs.append(log_entry)
        # Keep only the latest 50 logs
        if len(runtime_data.logs) > 50:
            runtime_data.logs.pop(0)

        # Thread-safety: this function can be called from outside the event loop.
        # Use the sync dispatcher helper.
        dispatcher_send(hass, signal)

    def _derive_filename_from_url(url: str) -> str:
        """Derive a safe-ish filename from a URL.

        Falls der URL-Pfad keinen Dateinamen enthält, wird ein Zeitstempel genutzt.
        """
        parsed = urlparse(url)
        name = (parsed.path or "").split("/")[-1]
        if not name:
            name = f"ha_{int(datetime.now().timestamp() * 1000)}.jpg"
        # Ensure we end up with something the device expects
        if "." not in name:
            name = f"{name}.jpg"
        return name

    def _sanitize_filename(name: str) -> str:
        """Make a device-safe filename (ASCII-ish, no spaces, enforce .jpg).

        We always upload JPEG bytes, so the device filename should be .jpg.
        """
        original = (name or "").strip()

        # Normalize unicode to ASCII where possible (e.g., umlauts).
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

    async def _download_bytes(url: str) -> bytes:
        """Download bytes from a URL."""
        session = async_get_clientsession(hass)
        async with async_timeout.timeout(30):
            async with session.get(url, allow_redirects=True) as resp:
                if resp.status != 200:
                    raise ValueError(f"download failed ({resp.status})")
                return await resp.read()

    def _looks_like_url(text: str) -> bool:
        t = (text or "").strip()
        return t.startswith("http://") or t.startswith("https://")

    def _decode_base64_image_bytes(value: Any) -> bytes:
        """Decode base64 that may include whitespace, missing padding, urlsafe alphabet or a data: URI."""
        text = ("" if value is None else str(value)).strip()
        if not text:
            raise ValueError("empty image_data")

        # Support data URIs like: data:image/png;base64,....
        if text.startswith("data:") and "," in text:
            text = text.split(",", 1)[1]

        # Remove whitespace/newlines
        text = "".join(text.split())

        # Add missing padding if needed
        pad = (-len(text)) % 4
        if pad:
            text = text + ("=" * pad)

        try:
            return base64.b64decode(text, validate=True)
        except (binascii.Error, ValueError):
            # Fallback: try urlsafe variant (common in some templating/SDKs)
            try:
                return base64.urlsafe_b64decode(text)
            except (binascii.Error, ValueError) as err:
                raise ValueError(f"invalid base64: {err}") from err

    async def _resolve_raw_image_bytes_from_service_field(value: Any) -> bytes:
        """Resolve raw image bytes from service field.

        Accepts either:
        - direct URL (http/https)
        - base64 image bytes
        - base64 of an URL (best-effort; common mistake)
        """
        # 1) Direct URL (common user expectation)
        if isinstance(value, str) and _looks_like_url(value):
            return await _download_bytes(value)

        # 2) Base64
        raw = _decode_base64_image_bytes(value)

        # 3) Best-effort: decoded bytes are actually a URL string
        try:
            as_text = raw.decode("utf-8", errors="strict").strip()
        except UnicodeDecodeError:
            return raw

        if _looks_like_url(as_text):
            return await _download_bytes(as_text)

        return raw

    async def _get_screen_resolution() -> tuple[int, int]:
        """Get screen resolution from device info, with safe fallback."""

        def _snap_to_supported_sizes(width: int, height: int) -> tuple[int, int]:
            """Snap (width,height) to known panel sizes.

            The public API docs for `/upload` require fixed pixel sizes:
            - 7.3 inch: 480x800
            - 13.3 inch: 1200x1600
            Some firmwares may report different/rotated values; we normalize to
            the nearest supported panel size to avoid upload/display issues.
            """
            w = int(width or 0)
            h = int(height or 0)
            if w <= 0 or h <= 0:
                return 1200, 1600

            # If one side is around 800/480-ish => 7.3" panel.
            if max(w, h) <= 900:
                return 480, 800

            # Default to 13.3" panel.
            return 1200, 1600

        # Prefer cached runtime data first (this is also what the diagnostic sensors are based on).
        device_info = runtime_data.device_info

        # If we have nothing cached yet, fetch once.
        if not device_info:
            # This helper is only used by upload services (user actions).
            # Allow BLE wake so uploads can proceed even if the device is asleep.
            device_info = await api_client.get_device_info(wake=True)
        if device_info:
            # If we just fetched new data, update shared runtime data + notify.
            if runtime_data.device_info is not device_info:
                runtime_data.device_info = device_info
                dispatcher_send(hass, signal)
            width = int(device_info.get("width", 1200) or 1200)
            height = int(device_info.get("height", 1600) or 1600)
            return _snap_to_supported_sizes(width, height)
        return _snap_to_supported_sizes(1200, 1600)

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
            # Rotate 90° clockwise so API still receives portrait data
            processed = processed.rotate(-90, expand=True)

        return processed

    async def _to_jpeg_bytes(
        raw: bytes,
        *,
        process: bool,
        screen_width: int,
        screen_height: int,
    ) -> bytes:
        """Convert incoming bytes to JPEG (optionally resize/crop) without blocking event loop."""
        orientation = entry.data.get(CONF_ORIENTATION, DEFAULT_ORIENTATION)
        fill_mode = entry.data.get(CONF_FILL_MODE, DEFAULT_FILL_MODE)
        contain_color = entry.data.get(CONF_CONTAIN_COLOR, DEFAULT_CONTAIN_COLOR)

        def _work() -> bytes:
            try:
                image = Image.open(BytesIO(raw))
            except Exception as err:
                # Give a more actionable error than just "cannot identify image file".
                # Common causes:
                # - not actually image bytes (e.g. URL/JSON/text)
                # - unsupported formats (e.g. HEIC/SVG)
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

    async def handle_show_next(call: ServiceCall) -> None:
        """Handle show next image service."""
        success = await api_client.show_next()
        if success:
            add_log("Successfully switched to next image")
        else:
            add_log("Failed to switch to next image", "error")

    async def handle_sleep(call: ServiceCall) -> None:
        """Handle device sleep service."""
        success = await api_client.sleep()
        if success:
            add_log("Device entered sleep mode")
        else:
            add_log("Device sleep failed", "error")

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle device reboot service."""
        success = await api_client.reboot()
        if success:
            add_log("Device reboot command sent")
        else:
            add_log("Device reboot failed", "error")

    async def handle_clear_screen(call: ServiceCall) -> None:
        """Handle clear screen service."""
        success = await api_client.clear_screen()
        if success:
            add_log("Screen cleared")
        else:
            add_log("Clear screen failed", "error")

    async def handle_whistle(call: ServiceCall) -> None:
        """Handle keep alive service."""
        success = await api_client.whistle()
        if success:
            add_log("Keep alive signal sent")
        else:
            add_log("Keep alive failed", "error")

    async def handle_update_settings(call: ServiceCall) -> None:
        """Handle update device settings service."""
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
            add_log("No settings parameters provided", "warning")
            return

        success = await api_client.update_settings(settings_data)
        if success:
            settings_str = ", ".join([f"{k}: {v}" for k, v in settings_data.items()])
            add_log(f"Device settings updated: {settings_str}")
        else:
            add_log("Settings update failed", "error")

    async def handle_refresh_device_info(call: ServiceCall) -> None:
        """Handle refresh device info service."""
        # Explicit user action: allow BLE wake.
        device_info = await api_client.get_device_info(wake=True)
        if device_info:
            runtime_data.device_info = device_info
            add_log("Device info refreshed")
            coordinator.async_set_updated_data(device_info)
            dispatcher_send(hass, signal)
        else:
            add_log("Failed to refresh device info", "error")

    async def handle_upload_image_url(call: ServiceCall) -> None:
        """Download an image from a URL, upload to the device, optionally show immediately."""
        url = call.data["url"]
        gallery = call.data.get("gallery", "default")
        filename = call.data.get("filename") or _derive_filename_from_url(url)
        safe_filename = _sanitize_filename(filename)
        if safe_filename != filename:
            add_log(f"Filename sanitized: '{filename}' -> '{safe_filename}'", "warning")
        filename = safe_filename
        show_now = bool(call.data.get("show_now", True))
        process = bool(call.data.get("process", True))

        try:
            raw = await _download_bytes(url)

            screen_w, screen_h = await _get_screen_resolution()
            jpeg = await _to_jpeg_bytes(raw, process=process, screen_width=screen_w, screen_height=screen_h)
            uploaded_path = await api_client.upload_image(
                jpeg,
                filename,
                gallery=gallery,
                show_now=show_now,
            )
            if uploaded_path:
                add_log(f"Uploaded image from URL to {uploaded_path}")
                # Best-effort refresh/push so entities update without waiting.
                device_info = await api_client.get_device_info(wake=True)
                if device_info:
                    runtime_data.device_info = device_info
                    coordinator.async_set_updated_data(device_info)
                    dispatcher_send(hass, signal)
            else:
                add_log(f"Upload failed for URL image: {url}", "error")
        except Exception as err:
            _LOGGER.exception("Error in upload_image_url service: %s", err)
            add_log(f"Upload image URL failed: {err}", "error")

    async def handle_upload_images_multi(call: ServiceCall) -> None:
        """Upload multiple images in one request via /image/uploadMulti."""
        gallery = call.data.get("gallery", "default")
        override = bool(call.data.get("override", False))
        process = bool(call.data.get("process", True))
        images_in = call.data.get("images")

        if not images_in:
            add_log("Missing images for upload_images_multi", "error")
            return

        # Home Assistant service UI may submit an "object" as dict. Accept both.
        if isinstance(images_in, dict):
            images_list: list[Any] = [images_in]
        elif isinstance(images_in, list):
            images_list = images_in
        else:
            add_log("Field 'images' must be a list or an object", "error")
            return

        screen_w, screen_h = await _get_screen_resolution()

        prepared: list[tuple[str, bytes]] = []
        skipped = 0

        for idx, item in enumerate(images_list):
            try:
                if not isinstance(item, dict):
                    skipped += 1
                    add_log(f"upload_images_multi: item #{idx} is not an object", "warning")
                    continue

                filename = item.get("filename")
                url = item.get("url")
                image_data_b64 = item.get("image_data")

                if not filename:
                    # derive filename from URL when possible
                    if url:
                        filename = _derive_filename_from_url(str(url))
                    else:
                        filename = f"ha_{int(datetime.now().timestamp() * 1000)}_{idx}.jpg"

                safe_filename = _sanitize_filename(str(filename))
                if safe_filename != filename:
                    add_log(
                        f"upload_images_multi: filename sanitized: '{filename}' -> '{safe_filename}'",
                        "warning",
                    )
                filename = safe_filename

                if url:
                    raw = await _download_bytes(str(url))
                elif image_data_b64:
                    normalized = "".join(str(image_data_b64).split())
                    raw = base64.b64decode(normalized, validate=True)
                else:
                    skipped += 1
                    add_log(
                        f"upload_images_multi: item #{idx} missing 'url' or 'image_data'",
                        "warning",
                    )
                    continue

                jpeg = await _to_jpeg_bytes(
                    raw,
                    process=process,
                    screen_width=screen_w,
                    screen_height=screen_h,
                )
                prepared.append((filename, jpeg))
            except Exception as err:
                skipped += 1
                _LOGGER.exception("upload_images_multi: failed to prepare item #%s: %s", idx, err)
                add_log(f"upload_images_multi: failed to prepare item #{idx}: {err}", "warning")

        if not prepared:
            add_log("upload_images_multi: no valid images to upload", "error")
            return

        success = await api_client.upload_images_multi(
            prepared,
            gallery=gallery,
            override=override,
        )
        if success:
            add_log(
                f"Uploaded {len(prepared)} images via uploadMulti to gallery '{gallery}' (skipped={skipped})"
            )
        else:
            add_log(
                f"upload_images_multi failed for gallery '{gallery}' (prepared={len(prepared)}, skipped={skipped})",
                "error",
            )

    async def handle_list_playlists(call: ServiceCall) -> dict[str, Any] | None:
        """List playlists via GET /playlist/list."""
        playlists = await api_client.get_playlists()
        add_log(f"Playlists: {len(playlists)}")
        if SupportsResponse is not None:
            return {"playlists": playlists}
        return None

    async def handle_get_playlist(call: ServiceCall) -> dict[str, Any] | None:
        """Get playlist via GET /playlist?name=..."""
        name = call.data.get("name")
        if not name:
            add_log("Missing name for get_playlist", "error")
            return {"error": "missing name"} if SupportsResponse is not None else None
        playlist = await api_client.get_playlist(str(name))
        if playlist is None:
            add_log(f"Get playlist failed: {name}", "error")
            if SupportsResponse is not None:
                return {"name": str(name), "playlist": None}
            return None
        add_log(f"Fetched playlist: {name}")
        if SupportsResponse is not None:
            return {"name": str(name), "playlist": playlist}
        return None

    async def handle_list_galleries(call: ServiceCall) -> dict[str, Any] | None:
        """List galleries via GET /gallery/list."""
        galleries = await api_client.get_galleries()
        add_log(f"Galleries: {len(galleries)}")
        if SupportsResponse is not None:
            return {"galleries": galleries}
        return None

    async def handle_upload_image_data(call: ServiceCall) -> None:
        """Upload an image provided as base64-encoded bytes."""
        image_data_b64 = call.data["image_data"]
        gallery = call.data.get("gallery", "default")
        filename = call.data.get("filename") or f"ha_{int(datetime.now().timestamp() * 1000)}.jpg"
        safe_filename = _sanitize_filename(filename)
        if safe_filename != filename:
            add_log(f"Filename sanitized: '{filename}' -> '{safe_filename}'", "warning")
        filename = safe_filename
        show_now = bool(call.data.get("show_now", True))
        process = bool(call.data.get("process", True))

        try:
            raw = await _resolve_raw_image_bytes_from_service_field(image_data_b64)
        except Exception as err:
            add_log(f"Invalid image_data (expected base64 or URL): {err}", "error")
            return

        try:
            screen_w, screen_h = await _get_screen_resolution()
            jpeg = await _to_jpeg_bytes(raw, process=process, screen_width=screen_w, screen_height=screen_h)
            uploaded_path = await api_client.upload_image(
                jpeg,
                filename,
                gallery=gallery,
                show_now=show_now,
            )
            if uploaded_path:
                add_log(f"Uploaded image data to {uploaded_path}")
                # Best-effort refresh/push so entities update without waiting.
                device_info = await api_client.get_device_info(wake=True)
                if device_info:
                    runtime_data.device_info = device_info
                    coordinator.async_set_updated_data(device_info)
                    dispatcher_send(hass, signal)
            else:
                add_log(f"Upload failed for image data: {filename}", "error")
        except Exception as err:
            _LOGGER.exception("Error in upload_image_data service: %s", err)
            add_log(f"Upload image data failed: {err}", "error")

    async def handle_upload_dithered_image_data(call: ServiceCall) -> None:
        """Upload dithered raw image data (advanced)."""
        dithered_b64 = call.data["dithered_image_data"]
        filename = call.data["filename"]

        try:
            normalized = "".join(str(dithered_b64).split())
            raw = base64.b64decode(normalized, validate=True)
        except Exception:
            add_log("Invalid base64 in dithered_image_data", "error")
            return

        success = await api_client.upload_dithered_image_data(raw, filename)
        if success:
            add_log(f"Uploaded dithered image data: {filename}")
        else:
            add_log(f"Dithered image data upload failed: {filename}", "error")

    async def handle_delete_image(call: ServiceCall) -> None:
        """Delete an image from a gallery."""
        filename = call.data.get("filename") or call.data.get("image")
        gallery = call.data.get("gallery", "default")

        if not filename:
            add_log("Missing filename for delete_image", "error")
            return

        success = await api_client.delete_image(filename, gallery=gallery)
        if success:
            add_log(f"Deleted image: {gallery}/{filename}")
        else:
            add_log(f"Delete image failed: {gallery}/{filename}", "error")

    async def handle_create_gallery(call: ServiceCall) -> None:
        """Create a gallery."""
        name = call.data.get("name")
        if not name:
            add_log("Missing name for create_gallery", "error")
            return

        success = await api_client.create_gallery(name)
        if success:
            add_log(f"Created gallery: {name}")
        else:
            add_log(f"Create gallery failed: {name}", "error")

    async def handle_delete_gallery(call: ServiceCall) -> None:
        """Delete a gallery."""
        name = call.data.get("name")
        if not name:
            add_log("Missing name for delete_gallery", "error")
            return

        success = await api_client.delete_gallery(name)
        if success:
            add_log(f"Deleted gallery: {name}")
        else:
            add_log(f"Delete gallery failed: {name}", "error")

    async def handle_show_playlist(call: ServiceCall) -> None:
        """Start playlist playback."""
        playlist = call.data.get("playlist")
        image = call.data.get("image")
        dither = call.data.get("dither")

        if not playlist:
            add_log("Missing playlist for show_playlist", "error")
            return

        success = await api_client.show_playlist(playlist, image=image, dither=dither)
        if success:
            add_log(f"Started playlist: {playlist}")
        else:
            add_log(f"Start playlist failed: {playlist}", "error")

    async def handle_put_playlist(call: ServiceCall) -> None:
        """Create/overwrite a playlist."""
        name = call.data.get("name")
        playlist_type = call.data.get("type")
        time_offset = call.data.get("time_offset")
        items = call.data.get("items")

        if not name or not playlist_type or items is None:
            add_log("Missing required fields for put_playlist (name, type, items)", "error")
            return

        payload: dict[str, Any] = {
            "name": name,
            "type": playlist_type,
            "list": items,
        }
        if time_offset is not None:
            payload["time_offset"] = time_offset

        success = await api_client.put_playlist(payload)
        if success:
            add_log(f"Playlist created/updated: {name}")
        else:
            add_log(f"Put playlist failed: {name}", "error")

    async def handle_delete_playlist(call: ServiceCall) -> None:
        """Delete a playlist."""
        name = call.data.get("name")
        if not name:
            add_log("Missing name for delete_playlist", "error")
            return

        success = await api_client.delete_playlist(name)
        if success:
            add_log(f"Deleted playlist: {name}")
        else:
            add_log(f"Delete playlist failed: {name}", "error")

    # Register all services
    # Tuple format:
    # (service_name, handler, schema_dict, supports_response)
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

        # Remove services
        services_to_remove = [
            "show_next", "sleep", "reboot", "clear_screen",
            "whistle", "refresh_device_info", "update_settings",
            "upload_image_url", "upload_image_data", "upload_images_multi", "upload_dithered_image_data",
            "delete_image", "create_gallery", "delete_gallery",
            "list_galleries",
            "show_playlist", "list_playlists", "get_playlist", "put_playlist", "delete_playlist",
        ]
        for service in services_to_remove:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)

    return unload_ok
