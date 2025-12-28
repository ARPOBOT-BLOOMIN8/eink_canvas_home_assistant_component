"""Support for BLOOMIN8 E-Ink Canvas."""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime
from io import BytesIO

from PIL import Image

from homeassistant.components.media_player import (
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    BrowseMedia,
    MediaClass,
    MediaType,
)
from homeassistant.components import media_source
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.dispatcher import async_dispatcher_connect, async_dispatcher_send
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)

from .const import (
    DOMAIN,
    DEFAULT_NAME,
    SIGNAL_DEVICE_INFO_UPDATED,
    CONF_ORIENTATION,
    CONF_FILL_MODE,
    CONF_CONTAIN_COLOR,
    ORIENTATION_LANDSCAPE,
    FILL_MODE_CONTAIN,
    FILL_MODE_COVER,
    FILL_MODE_AUTO,
    DEFAULT_ORIENTATION,
    DEFAULT_FILL_MODE,
    DEFAULT_CONTAIN_COLOR,
    CONTAIN_COLORS,
)

_LOGGER = logging.getLogger(__name__)


# Media-player cards (and other clients) may refresh images frequently. If they load
# the image directly from the device, each HTTP request can reset the device's idle
# timer and prevent auto-sleep. To avoid that, we serve images via HA's
# media_player proxy and cache the bytes in-memory for a short time.
_MEDIA_IMAGE_CACHE_TTL_SECONDS = 60


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas media player."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    coordinator = config_entry.runtime_data.coordinator

    # CoordinatorEntity.async_update triggers a coordinator refresh.
    # We already do an initial coordinator refresh during integration setup, so
    # update_before_add=True would cause an immediate extra HTTP fetch.
    async_add_entities([EinkDisplayMediaPlayer(coordinator, hass, config_entry, host, name)], False)


class EinkDisplayMediaPlayer(CoordinatorEntity, MediaPlayerEntity):
    """BLOOMIN8 E-Ink Canvas media player for displaying images."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA |
        MediaPlayerEntityFeature.BROWSE_MEDIA |
        MediaPlayerEntityFeature.NEXT_TRACK
    )

    def __init__(
        self,
        coordinator,
        hass: HomeAssistant,
        config_entry: ConfigEntry,
        host: str,
        name: str,
    ) -> None:
        """Initialize the media player."""
        super().__init__(coordinator)
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = name
        self._attr_name = "Media Player"
        self._attr_unique_id = f"eink_display_{host}_media_player"
        self._attr_state = MediaPlayerState.ON
        self._attr_media_content_type = MediaType.IMAGE
        # Force Home Assistant to proxy the image instead of handing out a direct
        # device URL to clients. This avoids clients repeatedly requesting the
        # device image and keeping the Canvas awake.
        self._attr_media_image_remotely_accessible = False
        self._attr_has_entity_name = True
        self._device_info = None
        self._screen_width = None
        self._screen_height = None
        # Updates are driven by the shared coordinator (if polling enabled) or by
        # action-driven refreshes that push new snapshots into the coordinator.
        self._attr_should_poll = False
        self._unsub_dispatcher = None
        self._signal = f"{SIGNAL_DEVICE_INFO_UPDATED}_{config_entry.entry_id}"

        # In-memory cache for the currently displayed image.
        self._media_image_cache_lock = asyncio.Lock()
        self._media_image_cache_path: str | None = None
        self._media_image_cache_bytes: bytes | None = None
        self._media_image_cache_content_type: str | None = None
        self._media_image_cache_fetched_at: float = 0.0

    async def async_added_to_hass(self) -> None:
        """Register callbacks when entity is added."""
        await super().async_added_to_hass()

        # Keep the legacy dispatcher for non-coordinator update paths.
        # (Thread-safety: schedule_update_ha_state is safe.)
        self._unsub_dispatcher = async_dispatcher_connect(
            self.hass,
            self._signal,
            self._handle_runtime_data_updated,
        )

        # Apply cached device info once on startup so the entity is usable
        # immediately after HA restart even if the device is asleep.
        self._handle_coordinator_update()

    async def async_will_remove_from_hass(self) -> None:
        """Clean up callbacks."""
        if self._unsub_dispatcher is not None:
            self._unsub_dispatcher()
            self._unsub_dispatcher = None
        await super().async_will_remove_from_hass()

    @callback
    def _handle_runtime_data_updated(self) -> None:
        """Handle runtime data updates (no network I/O)."""
        # Thread-safety: use sync helper safe from any thread.
        self.schedule_update_ha_state(False)

    @callback
    def _handle_coordinator_update(self) -> None:
        """Handle coordinator updates (no network I/O)."""
        runtime_data = self._config_entry.runtime_data
        self._apply_device_info(self.coordinator.data or runtime_data.device_info, notify=False)
        super()._handle_coordinator_update()

    def _apply_device_info(self, device_info: dict | None, *, notify: bool) -> None:
        """Apply device info to entity and shared runtime data."""
        runtime_data = self._config_entry.runtime_data

        if device_info:
            self._device_info = device_info
            self._attr_state = MediaPlayerState.ON

            # Store screen resolution on first update
            if self._screen_width is None and self._screen_height is None:
                self._screen_width = device_info.get("width", 1200)
                self._screen_height = device_info.get("height", 1600)
                _LOGGER.info(
                    "Detected screen resolution: %dx%d",
                    self._screen_width,
                    self._screen_height,
                )

            runtime_data.device_info = device_info
        else:
            self._attr_state = MediaPlayerState.OFF
            self._device_info = None

        if notify:
            async_dispatcher_send(self.hass, self._signal)

    async def _async_fetch_device_info(self) -> None:
        """Fetch device info from device on-demand (even if polling is disabled)."""
        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        # On-demand fetch is user/action-driven: allow BLE wake.
        device_info = await api_client.get_device_info(wake=True)
        # Push snapshot into coordinator so other entities update even when
        # periodic polling is disabled.
        runtime_data.coordinator.async_set_updated_data(device_info)
        self._apply_device_info(device_info, notify=True)

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, self._host)},
            name=self._device_name,
            manufacturer="BLOOMIN8",
            model="E-Ink Canvas",
        )

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if not self._device_info:
            return {}

        return {
            "device_name": self._device_info.get("name"),
            "current_image": self._device_info.get("image", "").split("/")[-1] if self._device_info.get("image") else "None",
            "battery_level": f"{self._device_info.get('battery', 0)}%",
            "wifi_network": self._device_info.get("sta_ssid"),
            "ip_address": self._device_info.get("sta_ip"),
            "gallery": self._device_info.get("gallery"),
            "screen_resolution": f"{self._device_info.get('width', 0)}x{self._device_info.get('height', 0)}",
        }

    @property
    def media_image_url(self) -> str | None:
        """Return the current image URL for display."""
        if self._device_info and self._device_info.get("image"):
            return f"http://{self._host}{self._device_info['image']}"
        return None

    async def async_get_media_image(self) -> tuple[bytes, str] | None:
        """Return bytes for the current media image.

        Home Assistant will serve this through the media_player proxy endpoint.
        We keep a small in-memory cache to avoid hitting the device repeatedly
        if the UI refreshes the image frequently.
        """
        image_path: str | None = None
        if self._device_info:
            image_path = self._device_info.get("image")

        if not image_path:
            return None

        now = time.monotonic()

        async with self._media_image_cache_lock:
            cached_path = self._media_image_cache_path
            cached_bytes = self._media_image_cache_bytes
            cached_type = self._media_image_cache_content_type
            cached_at = self._media_image_cache_fetched_at

            # Fresh cache hit
            if (
                cached_path == image_path
                and cached_bytes is not None
                and cached_type is not None
                and (now - cached_at) < _MEDIA_IMAGE_CACHE_TTL_SECONDS
            ):
                _LOGGER.debug(
                    "Serving cached media image via HA proxy (path=%s, age=%.1fs)",
                    image_path,
                    now - cached_at,
                )
                return (cached_bytes, cached_type)

            runtime_data = self._config_entry.runtime_data
            api_client = runtime_data.api_client

            # By default we do not want UI image refreshes to keep the device awake.
            # However, if the user explicitly enabled BLE auto-wake, we allow an
            # "auto" wake here so the UI can still show the current image when
            # the device is asleep.
            _LOGGER.debug(
                "Fetching media image from device for HA proxy (path=%s)", image_path
            )
            image_bytes = await api_client.get_image_bytes(image_path, wake=None)

            if not image_bytes:
                # If the device is asleep/offline, returning the last cached
                # image is better than repeatedly trying (which could keep it
                # awake once it wakes).
                if cached_path == image_path and cached_bytes is not None and cached_type is not None:
                    _LOGGER.debug(
                        "Device image fetch failed; serving stale cached image (path=%s)",
                        image_path,
                    )
                    return (cached_bytes, cached_type)
                return None

            # Guess content-type from extension.
            lower = image_path.lower()
            if lower.endswith(".png"):
                content_type = "image/png"
            elif lower.endswith(".gif"):
                content_type = "image/gif"
            elif lower.endswith(".bmp"):
                content_type = "image/bmp"
            elif lower.endswith(".webp"):
                content_type = "image/webp"
            else:
                content_type = "image/jpeg"

            self._media_image_cache_path = image_path
            self._media_image_cache_bytes = image_bytes
            self._media_image_cache_content_type = content_type
            self._media_image_cache_fetched_at = now

            return (image_bytes, content_type)

    @property
    def media_title(self) -> str | None:
        """Return the current media title."""
        if not self._device_info or not self._device_info.get("image"):
            return None

        image_path = self._device_info.get("image", "")
        return image_path.split("/")[-1] if "/" in image_path else image_path

    async def async_update(self) -> None:
        """Update entity state from cached coordinator/runtime data (no I/O)."""
        runtime_data = self._config_entry.runtime_data
        self._apply_device_info(self.coordinator.data or runtime_data.device_info, notify=False)

    async def async_turn_on(self) -> None:
        """Turn on the device (send whistle)."""
        await self.hass.services.async_call(
            DOMAIN,
            "whistle",
            {},
            blocking=True,
        )

    async def async_turn_off(self) -> None:
        """Turn off the device (sleep)."""
        await self.hass.services.async_call(
            DOMAIN,
            "sleep",
            {},
            blocking=True,
        )

    async def async_media_next_track(self) -> None:
        """Play next track (show next image)."""
        await self.hass.services.async_call(
            DOMAIN,
            "show_next",
            {},
            blocking=True,
        )

    async def async_play_media(self, media_type: str, media_id: str, **kwargs) -> None:
        """Play media - show image using /show API."""
        if not media_type.startswith("image/"):
            _LOGGER.error("Only images are supported, got: %s", media_type)
            return

        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        try:
            # Add log
            await self._add_log(f"Playing media: {media_id}")

            # Handle media source resolution first
            if media_source.is_media_source_id(media_id):
                play_item = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id
                )

                media_id = async_process_play_media_url(self.hass, play_item.url)
                _LOGGER.debug("Using media URL: %s", media_id)

            # Ensure we have screen resolution
            if self._screen_width is None or self._screen_height is None:
                await self._async_fetch_device_info()

            if self._screen_width is None or self._screen_height is None:
                await self._add_log("Failed to detect screen resolution", "error")
                return

            # Guard clause: Handle gallery images directly
            if media_id.startswith("/gallerys/"):
                success = await api_client.show_image(media_id)
                if success:
                    await self._add_log(f"Successfully displayed image via /show API: {media_id}")
                else:
                    await self._add_log(f"Failed to show image: {media_id}", "error")
                await self._async_fetch_device_info()
                return

            # Handle external images - upload and show
            image_data = await self._load_image_data(media_id)
            if not image_data:
                await self._add_log(f"Failed to load image: {media_id}", "error")
                return

            # Process image for e-ink display
            processed_image_data = await self._process_image(image_data)
            if not processed_image_data:
                await self._add_log("Failed to process image", "error")
                return

            # Generate filename and upload
            filename = f"ha_{int(time.time() * 1000)}.jpg"
            gallery = "default"
            uploaded_path = await api_client.upload_image(processed_image_data, filename, gallery=gallery)
            if not uploaded_path:
                await self._add_log(f"Upload failed: {filename}", "error")
                return

            await self._add_log(f"Successfully uploaded image: {uploaded_path}")

            # Show the uploaded image - use play_type=0 (single image mode)
            success = await api_client.show_image_by_name(filename, gallery, play_type=0)
            if success:
                await self._add_log(f"Successfully displayed uploaded image: {filename}")
            else:
                await self._add_log(f"Failed to show uploaded image: {filename}", "error")

            # Refresh device info
            await self._async_fetch_device_info()

        except Exception as err:
            await self._add_log(f"Error playing media: {str(err)}", "error")
            _LOGGER.exception("Error playing media: %s", err)

    async def _load_image_data(self, media_id: str) -> bytes | None:
        """Load image data from file or URL."""
        # Handle URL
        if not media_id.startswith("/"):
            session = async_get_clientsession(self.hass)
            async with session.get(media_id) as response:
                if response.status != 200:
                    _LOGGER.error("Failed to download image from URL: %s", response.status)
                    return None
                return await response.read()

        # Handle local file
        if not await self.hass.async_add_executor_job(os.path.exists, media_id):
            _LOGGER.error("File does not exist: %s", media_id)
            return None

        def read_file():
            with open(media_id, "rb") as f:
                return f.read()

        return await self.hass.async_add_executor_job(read_file)

    async def _process_image(self, image_data: bytes) -> bytes | None:
        """Process image for e-ink display with orientation and fill mode support."""
        try:
            image = Image.open(BytesIO(image_data))
            _LOGGER.debug("Processing image: %s, size: %s", image.format, image.size)

            # Get configuration
            orientation = self._config_entry.data.get(CONF_ORIENTATION, DEFAULT_ORIENTATION)
            fill_mode = self._config_entry.data.get(CONF_FILL_MODE, DEFAULT_FILL_MODE)
            contain_color = self._config_entry.data.get(CONF_CONTAIN_COLOR, DEFAULT_CONTAIN_COLOR)

            _LOGGER.debug(
                "Image processing config - orientation: %s, fill_mode: %s, contain_color: %s",
                orientation, fill_mode, contain_color
            )

            # Convert to RGB if needed
            image = await self.hass.async_add_executor_job(
                self._convert_to_rgb, image
            )

            # Process image with orientation and fill mode
            image = await self.hass.async_add_executor_job(
                self._process_with_orientation,
                image,
                orientation,
                fill_mode,
                contain_color
            )

            # Convert to JPEG
            def save_image():
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=95)
                return img_byte_arr.getvalue()

            return await self.hass.async_add_executor_job(save_image)

        except Exception as err:
            _LOGGER.exception("Error processing image: %s", err)
            return None

    def _convert_to_rgb(self, image: Image.Image) -> Image.Image:
        """Convert image to RGB format."""
        if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
            background = Image.new('RGB', image.size, (255, 255, 255))
            if image.mode == 'P':
                image = image.convert('RGBA')
            background.paste(image, mask=image.split()[-1])
            return background
        elif image.mode != 'RGB':
            return image.convert('RGB')
        return image

    def _hex_to_rgb(self, hex_color: str) -> tuple[int, int, int]:
        """Convert hex color string to RGB tuple."""
        hex_color = hex_color.lstrip('#')
        if len(hex_color) != 6:
            return (255, 255, 255)  # Default to white
        try:
            return (
                int(hex_color[0:2], 16),
                int(hex_color[2:4], 16),
                int(hex_color[4:6], 16),
            )
        except ValueError:
            return (255, 255, 255)

    def _process_with_orientation(
        self,
        image: Image.Image,
        orientation: str,
        fill_mode: str,
        contain_color: str
    ) -> Image.Image:
        """Process image based on orientation and fill mode settings.

        The device API always expects portrait images. For landscape orientation,
        we create a landscape-oriented image then rotate it 90° clockwise.
        """
        # Screen resolution (always portrait from device)
        screen_width = self._screen_width or 1200   # e.g., 1200
        screen_height = self._screen_height or 1600  # e.g., 1600

        # Determine canvas dimensions based on orientation
        canvas_is_landscape = (orientation == ORIENTATION_LANDSCAPE)
        if canvas_is_landscape:
            # For landscape, we work with swapped dimensions first
            target_width = screen_height  # 1600
            target_height = screen_width  # 1200
        else:
            target_width = screen_width   # 1200
            target_height = screen_height  # 1600

        # Determine if image is landscape
        image_is_landscape = image.width > image.height

        # Determine actual fill mode
        if fill_mode == FILL_MODE_AUTO:
            # Same orientation -> cover, different -> contain
            if image_is_landscape == canvas_is_landscape:
                actual_fill_mode = FILL_MODE_COVER
            else:
                actual_fill_mode = FILL_MODE_CONTAIN
        else:
            actual_fill_mode = fill_mode

        _LOGGER.debug(
            "Processing: image %dx%d (%s), canvas %dx%d (%s), fill_mode: %s -> %s",
            image.width, image.height,
            "landscape" if image_is_landscape else "portrait",
            target_width, target_height,
            "landscape" if canvas_is_landscape else "portrait",
            fill_mode, actual_fill_mode
        )

        # Apply fill mode
        if actual_fill_mode == FILL_MODE_COVER:
            processed = self._cover_image(image, target_width, target_height)
        else:  # FILL_MODE_CONTAIN
            # Convert color key (e.g., "white") to hex value (e.g., "#FFFFFF")
            hex_color = CONTAIN_COLORS.get(contain_color, "#FFFFFF")
            bg_color = self._hex_to_rgb(hex_color)
            processed = self._contain_image(image, target_width, target_height, bg_color)

        # If landscape orientation, rotate 90° clockwise to make it portrait for API
        if canvas_is_landscape:
            processed = processed.rotate(-90, expand=True)
            _LOGGER.debug("Rotated image for landscape display: %dx%d", processed.width, processed.height)

        _LOGGER.debug("Final processed image size: %dx%d", processed.width, processed.height)
        return processed

    def _cover_image(
        self,
        image: Image.Image,
        target_width: int,
        target_height: int
    ) -> Image.Image:
        """Scale and crop image to cover the target area (center crop)."""
        image_aspect = image.width / image.height
        target_aspect = target_width / target_height

        if image_aspect > target_aspect:
            # Image is wider - fit to height, crop width
            scaled_height = target_height
            scaled_width = int(target_height * image_aspect)
        else:
            # Image is taller - fit to width, crop height
            scaled_width = target_width
            scaled_height = int(target_width / image_aspect)

        # Scale image
        scaled_image = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

        # Center crop
        x_offset = (scaled_width - target_width) // 2
        y_offset = (scaled_height - target_height) // 2
        return scaled_image.crop((
            x_offset,
            y_offset,
            x_offset + target_width,
            y_offset + target_height
        ))

    def _contain_image(
        self,
        image: Image.Image,
        target_width: int,
        target_height: int,
        bg_color: tuple[int, int, int]
    ) -> Image.Image:
        """Scale image to fit within target area, fill remaining with background color."""
        image_aspect = image.width / image.height
        target_aspect = target_width / target_height

        if image_aspect > target_aspect:
            # Image is wider - fit to width
            scaled_width = target_width
            scaled_height = int(target_width / image_aspect)
        else:
            # Image is taller - fit to height
            scaled_height = target_height
            scaled_width = int(target_height * image_aspect)

        # Scale image
        scaled_image = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

        # Create background and paste centered
        background = Image.new('RGB', (target_width, target_height), bg_color)
        x_offset = (target_width - scaled_width) // 2
        y_offset = (target_height - scaled_height) // 2
        background.paste(scaled_image, (x_offset, y_offset))

        return background

    async def async_browse_media(self, media_content_type: str | None = None, media_content_id: str | None = None) -> BrowseMedia:
        """Browse media - show device galleries and local media."""
        try:
            if media_content_id is None:
                return await self._browse_root()
            elif media_content_id == "device_galleries":
                return await self._browse_galleries()
            elif media_content_id.startswith("gallery:"):
                gallery_name = media_content_id[8:]
                return await self._browse_gallery_images(gallery_name)
            elif media_content_id == "local_media":
                # Browse Home Assistant's configured local media.
                # Passing None is not supported consistently across HA versions and
                # can raise "Media directory does not exist".
                from homeassistant.components.media_source import local_source
                from homeassistant.components import media_source as ha_media_source

                if hasattr(local_source, "async_browse_media"):
                    return await local_source.async_browse_media(
                        self.hass,
                        "",
                        content_filter=lambda item: item.media_content_type.startswith("image/"),
                    )

                if hasattr(ha_media_source, "generate_media_source_id"):
                    # HA API signature differs across versions:
                    # - older: generate_media_source_id(domain)
                    # - newer: generate_media_source_id(domain, identifier)
                    try:
                        local_root = ha_media_source.generate_media_source_id(local_source.DOMAIN, "")
                    except TypeError:
                        local_root = ha_media_source.generate_media_source_id(local_source.DOMAIN)
                    return await ha_media_source.async_browse_media(
                        self.hass,
                        local_root,
                        content_filter=lambda item: item.media_content_type.startswith("image/"),
                    )

                raise ValueError(
                    "Browsing local media is not available in this Home Assistant version."
                )
            else:
                return await media_source.async_browse_media(
                    self.hass,
                    media_content_id,
                    content_filter=lambda item: item.media_content_type.startswith('image/')
                )
        except Exception as err:
            # This often happens when Home Assistant has no media directory configured.
            # Avoid spamming ERROR logs for an expected setup condition.
            msg = str(err)
            if "Media directory" in msg and "does not exist" in msg:
                _LOGGER.warning(
                    "Media browsing unavailable: %s. Configure a media directory in Home Assistant to use Local Media.",
                    msg,
                )
            else:
                _LOGGER.error("Error browsing media: %s", msg)
            return BrowseMedia(
                title="Error",
                media_class=MediaClass.DIRECTORY,
                media_content_type="directory",
                media_content_id="",
                can_play=False,
                can_expand=False,
                children=[],
            )

    async def _browse_root(self) -> BrowseMedia:
        """Browse root level - show device galleries and local media."""
        children = [
            BrowseMedia(
                title="Device Galleries",
                media_class=MediaClass.DIRECTORY,
                media_content_type="directory",
                media_content_id="device_galleries",
                can_play=False,
                can_expand=True,
                thumbnail=None,
            ),
            BrowseMedia(
                title="Local Media",
                media_class=MediaClass.DIRECTORY,
                media_content_type="directory",
                media_content_id="local_media",
                can_play=False,
                can_expand=True,
                thumbnail=None,
            ),
        ]

        return BrowseMedia(
            title="Media Browser",
            media_class=MediaClass.DIRECTORY,
            media_content_type="directory",
            media_content_id="",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_galleries(self) -> BrowseMedia:
        """Browse available galleries."""
        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        galleries_data = await api_client.get_galleries()
        children = []

        for gallery in galleries_data:
            gallery_name = gallery.get("name", "")
            children.append(BrowseMedia(
                title=gallery_name,
                media_class=MediaClass.DIRECTORY,
                media_content_type="directory",
                media_content_id=f"gallery:{gallery_name}",
                can_play=False,
                can_expand=True,
                thumbnail=None,
            ))

        return BrowseMedia(
            title="Device Galleries",
            media_class=MediaClass.DIRECTORY,
            media_content_type="directory",
            media_content_id="device_galleries",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _browse_gallery_images(self, gallery_name: str) -> BrowseMedia:
        """Browse images in a specific gallery."""
        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        gallery_data = await api_client.get_gallery_images(gallery_name)
        children = []

        for image in gallery_data.get("data", []):
            image_name = image.get("name", "")
            image_path = f"/gallerys/{gallery_name}/{image_name}"

            children.append(BrowseMedia(
                title=image_name,
                media_class=MediaClass.IMAGE,
                media_content_type="image/jpeg",
                media_content_id=image_path,
                can_play=True,
                can_expand=False,
                thumbnail=f"http://{self._host}{image_path}",
            ))

        return BrowseMedia(
            title=f"Gallery: {gallery_name}",
            media_class=MediaClass.DIRECTORY,
            media_content_type="directory",
            media_content_id=f"gallery:{gallery_name}",
            can_play=False,
            can_expand=True,
            children=children,
        )

    async def _add_log(self, message: str, level: str = "info") -> None:
        """Add log entry."""
        runtime_data = self._config_entry.runtime_data
        runtime_data.logs.append({
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        })
        if len(runtime_data.logs) > 50:
            runtime_data.logs.pop(0)
