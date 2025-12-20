"""Support for BLOOMIN8 E-Ink Canvas."""
from __future__ import annotations

import logging
import os
from io import BytesIO
from PIL import Image
import time

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
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.components.media_player.browse_media import (
    async_process_play_media_url,
)

from .const import (
    DOMAIN,
    DEFAULT_NAME,
)

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the BLOOMIN8 E-Ink Canvas media player."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    async_add_entities([EinkDisplayMediaPlayer(hass, config_entry, host, name)], True)


class EinkDisplayMediaPlayer(MediaPlayerEntity):
    """BLOOMIN8 E-Ink Canvas media player for displaying images."""

    _attr_supported_features = (
        MediaPlayerEntityFeature.PLAY_MEDIA |
        MediaPlayerEntityFeature.BROWSE_MEDIA |
        MediaPlayerEntityFeature.NEXT_TRACK |
        MediaPlayerEntityFeature.TURN_ON |
        MediaPlayerEntityFeature.TURN_OFF
    )

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, name: str) -> None:
        """Initialize the media player."""
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = name
        self._attr_name = "Media Player"
        self._attr_unique_id = f"eink_display_{host}_media_player"
        self._attr_state = MediaPlayerState.ON
        self._attr_media_content_type = MediaType.IMAGE
        self._attr_media_image_remotely_accessible = True
        self._attr_has_entity_name = True
        self._device_info = None
        self._screen_width = None
        self._screen_height = None

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

    @property
    def media_title(self) -> str | None:
        """Return the current media title."""
        if not self._device_info or not self._device_info.get("image"):
            return None

        image_path = self._device_info.get("image", "")
        return image_path.split("/")[-1] if "/" in image_path else image_path

    async def async_update(self) -> None:
        """Update device state and information."""
        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        device_info = await api_client.get_device_info()
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
                    self._screen_height
                )

            # Update shared runtime data
            runtime_data.device_info = device_info
        else:
            self._attr_state = MediaPlayerState.OFF
            self._device_info = None

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
                _LOGGER.info("Using media URL: %s", media_id)

            # Ensure we have screen resolution
            if self._screen_width is None or self._screen_height is None:
                await self.async_update()

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
                await self.async_update()
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
            await self.async_update()

        except Exception as err:
            await self._add_log(f"Error playing media: {str(err)}", "error")
            _LOGGER.error("Error playing media: %s", str(err))

    async def _load_image_data(self, media_id: str) -> bytes | None:
        """Load image data from file or URL."""
        # Guard clause: Check if it's a local file
        if not media_id.startswith("/"):
            # Download from URL
            import aiohttp
            async with aiohttp.ClientSession() as session:
                async with session.get(media_id) as response:
                    if response.status != 200:
                        _LOGGER.error("Failed to download image from URL")
                        return None
                    return await response.read()

        # Guard clause: Check file exists
        file_exists = await self.hass.async_add_executor_job(os.path.exists, media_id)
        if not file_exists:
            _LOGGER.error("File does not exist: %s", media_id)
            return None

        def read_file():
            with open(media_id, "rb") as f:
                return f.read()

        return await self.hass.async_add_executor_job(read_file)

    async def _process_image(self, image_data: bytes) -> bytes | None:
        """Process image for e-ink display."""
        try:
            image = Image.open(BytesIO(image_data))
            _LOGGER.info("Processing image: %s, size: %s", image.format, image.size)

            # Convert to RGB if needed
            image = await self.hass.async_add_executor_job(
                self._convert_to_rgb, image
            )

            # Scale and crop to fit display
            image = await self.hass.async_add_executor_job(
                self._scale_and_crop, image
            )

            # Convert to JPEG
            def save_image():
                img_byte_arr = BytesIO()
                image.save(img_byte_arr, format='JPEG', quality=95)
                return img_byte_arr.getvalue()

            return await self.hass.async_add_executor_job(save_image)

        except Exception as err:
            _LOGGER.error("Error processing image: %s", err)
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

    def _scale_and_crop(self, image: Image.Image) -> Image.Image:
        """Scale and crop image to fit display."""
        # Use stored screen resolution
        target_width = self._screen_width
        target_height = self._screen_height

        _LOGGER.info(
            "Resizing image from %dx%d to target %dx%d",
            image.width,
            image.height,
            target_width,
            target_height
        )

        image_aspect_ratio = image.width / image.height
        target_aspect_ratio = target_width / target_height

        if image_aspect_ratio > target_aspect_ratio:
            # Image is wider - fit to height
            scaled_height = target_height
            scaled_width = int(target_height * image_aspect_ratio)
        else:
            # Image is taller - fit to width
            scaled_width = target_width
            scaled_height = int(target_width / image_aspect_ratio)

        # Scale image
        scaled_image = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

        # Crop to target size
        x_offset = (scaled_width - target_width) // 2
        y_offset = (scaled_height - target_height) // 2
        final_image = scaled_image.crop((
            x_offset,
            y_offset,
            x_offset + target_width,
            y_offset + target_height
        ))

        _LOGGER.info("Final processed image size: %dx%d", final_image.width, final_image.height)
        return final_image

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
                return await media_source.async_browse_media(
                    self.hass,
                    None,
                    content_filter=lambda item: item.media_content_type.startswith('image/')
                )
            else:
                return await media_source.async_browse_media(
                    self.hass,
                    media_content_id,
                    content_filter=lambda item: item.media_content_type.startswith('image/')
                )
        except Exception as err:
            _LOGGER.error("Error browsing media: %s", str(err))
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
        from datetime import datetime

        log_entry = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        }

        runtime_data = self._config_entry.runtime_data
        runtime_data.logs.append(log_entry)
        # Keep only recent 50 logs
        if len(runtime_data.logs) > 50:
            runtime_data.logs.pop(0)
