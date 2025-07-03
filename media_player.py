"""Support for BLOOMIN8 E-Ink Canvas."""
from __future__ import annotations

import logging
import aiohttp
import async_timeout
import voluptuous as vol
from io import BytesIO
from PIL import Image
import os

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

from .const import (
    DOMAIN,
    ENDPOINT_UPLOAD,
    ENDPOINT_DEVICE_INFO,
    IMAGE_WIDTH,
    IMAGE_HEIGHT,
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

    @property
    def extra_state_attributes(self) -> dict:
        """Return additional state attributes."""
        if self._device_info:
            return {
                "device_name": self._device_info.get("name"),
                "current_image": self._device_info.get("image", "").split("/")[-1] if self._device_info.get("image") else "None",
                "battery_level": f"{self._device_info.get('battery', 0)}%",
                "wifi_network": self._device_info.get("sta_ssid"),
                "ip_address": self._device_info.get("sta_ip"),
                "gallery": self._device_info.get("gallery"),
                "screen_resolution": f"{self._device_info.get('width', 0)}x{self._device_info.get('height', 0)}",
            }
        return {}

    @property
    def media_image_url(self) -> str | None:
        """Return the current image URL for display."""
        if self._device_info and self._device_info.get("image"):
            return f"http://{self._host}{self._device_info['image']}"
        return None

    @property
    def media_title(self) -> str | None:
        """Return the current media title."""
        if self._device_info and self._device_info.get("image"):
            image_path = self._device_info.get("image", "")
            return image_path.split("/")[-1] if "/" in image_path else image_path
        return None

    async def async_update(self) -> None:
        """Update device state and information."""
        try:
            # Get device info
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://{self._host}{ENDPOINT_DEVICE_INFO}") as response:
                        if response.status == 200:
                            # Check content type and handle accordingly
                            content_type = response.headers.get('content-type', '')
                            if 'application/json' in content_type:
                                self._device_info = await response.json()
                            else:
                                # Handle non-JSON response
                                text_response = await response.text()
                                _LOGGER.warning("Received non-JSON response from device: %s", text_response[:200])
                                # Try to extract JSON from the response
                                try:
                                    import json
                                    # Look for JSON-like content
                                    start = text_response.find('{')
                                    end = text_response.rfind('}') + 1
                                    if start >= 0 and end > start:
                                        json_str = text_response[start:end]
                                        self._device_info = json.loads(json_str)
                                    else:
                                        self._device_info = None
                                except Exception as json_err:
                                    _LOGGER.error("Failed to parse device response: %s", json_err)
                                    self._device_info = None
                            
                            if self._device_info:
                                self._attr_state = MediaPlayerState.ON
                                # Update shared data
                                if DOMAIN in self.hass.data and self._config_entry.entry_id in self.hass.data[DOMAIN]:
                                    self.hass.data[DOMAIN][self._config_entry.entry_id]["device_info"] = self._device_info
                            else:
                                self._attr_state = MediaPlayerState.OFF
                        else:
                            self._attr_state = MediaPlayerState.OFF
                            self._device_info = None
        except Exception as err:
            _LOGGER.debug("Error updating device info: %s", str(err))
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

        try:
            # Add log
            await self._add_log(f"Playing media: {media_id}")

            # Handle media source resolution first
            if media_source.is_media_source_id(media_id):
                play_item = await media_source.async_resolve_media(
                    self.hass, media_id, self.entity_id
                )
                media_id = play_item.url
                _LOGGER.info("Resolved media path: %s", media_id)

            # If this is a gallery image path, show it directly using the /show API
            if media_id.startswith("/gallerys/"):
                # Use the show API to display the image
                async with aiohttp.ClientSession() as session:
                    show_data = {
                        "play_type": 0,  # Single image
                        "image": media_id
                    }
                    async with session.post(f"http://{self._host}/show", json=show_data) as response:
                        if response.status == 200:
                            await self._add_log(f"Successfully displayed image via /show API: {media_id}")
                            _LOGGER.info("Successfully displayed image via /show API: %s", media_id)
                        else:
                            response_text = await response.text()
                            await self._add_log(f"Failed to show image: {response.status} - {response_text}", "error")
                            _LOGGER.error("Failed to show image: %s - %s", response.status, response_text)
                            return
            else:
                # Handle file upload for external images
                # Handle local files
                if media_id.startswith("/"):
                    # Convert /media/local/ to actual file path
                    if media_id.startswith("/media/local/"):
                        media_id = f"/media/{media_id[13:]}"
                    
                    if not os.path.exists(media_id):
                        await self._add_log(f"File does not exist: {media_id}", "error")
                        _LOGGER.error("File does not exist: %s", media_id)
                        return
                    
                    with open(media_id, "rb") as f:
                        image_data = f.read()
                else:
                    # Download from URL
                    async with aiohttp.ClientSession() as session:
                        async with session.get(media_id) as response:
                            if response.status != 200:
                                await self._add_log("Failed to download image from URL", "error")
                                _LOGGER.error("Failed to download image from URL")
                                return
                            image_data = await response.read()

                # Process image for e-ink display
                image = Image.open(BytesIO(image_data))
                _LOGGER.info("Processing image: %s, size: %s", image.format, image.size)
                
                # Convert to RGB if needed
                if image.mode in ('RGBA', 'LA') or (image.mode == 'P' and 'transparency' in image.info):
                    background = Image.new('RGB', image.size, (255, 255, 255))
                    if image.mode == 'P':
                        image = image.convert('RGBA')
                    background.paste(image, mask=image.split()[-1])
                    image = background
                elif image.mode != 'RGB':
                    image = image.convert('RGB')

                # Scale and crop to fit display
                image_aspect_ratio = image.width / image.height
                target_aspect_ratio = IMAGE_WIDTH / IMAGE_HEIGHT

                if image_aspect_ratio > target_aspect_ratio:
                    # Image is wider - fit to height
                    scaled_height = IMAGE_HEIGHT
                    scaled_width = int(IMAGE_HEIGHT * image_aspect_ratio)
                else:
                    # Image is taller - fit to width
                    scaled_width = IMAGE_WIDTH
                    scaled_height = int(IMAGE_WIDTH / image_aspect_ratio)

                # Scale image
                scaled_image = image.resize((scaled_width, scaled_height), Image.Resampling.LANCZOS)

                # Crop to target size
                x_offset = (scaled_width - IMAGE_WIDTH) // 2
                y_offset = (scaled_height - IMAGE_HEIGHT) // 2
                cropped_image = scaled_image.crop((
                    x_offset,
                    y_offset,
                    x_offset + IMAGE_WIDTH,
                    y_offset + IMAGE_HEIGHT
                ))

                # Convert to JPEG
                img_byte_arr = BytesIO()
                cropped_image.save(img_byte_arr, format='JPEG', quality=95)
                processed_image_data = img_byte_arr.getvalue()

                # Generate filename
                import time
                filename = f"ha_{int(time.time() * 1000)}.jpg"

                # Upload to device and show immediately using /show API
                async with aiohttp.ClientSession() as session:
                    # First upload the image
                    form = aiohttp.FormData()
                    form.add_field(
                        'image',
                        processed_image_data,
                        filename=filename,
                        content_type='image/jpeg'
                    )
                    
                    upload_url = f"http://{self._host}{ENDPOINT_UPLOAD}?filename={filename}&gallery=default&show_now=0"
                    
                    async with session.post(upload_url, data=form) as response:
                        if response.status != 200:
                            response_text = await response.text()
                            await self._add_log(f"Upload failed: {response.status} - {response_text}", "error")
                            _LOGGER.error("Upload failed: %s - %s", response.status, response_text)
                            return
                        
                        await self._add_log(f"Successfully uploaded image: {filename}")
                        _LOGGER.info("Successfully uploaded image: %s", filename)

                    # Then use /show API to display it
                    show_data = {
                        "play_type": 0,  # Single image
                        "image": f"/gallerys/default/{filename}"
                    }
                    async with session.post(f"http://{self._host}/show", json=show_data) as response:
                        if response.status == 200:
                            await self._add_log(f"Successfully displayed uploaded image via /show API: {filename}")
                            _LOGGER.info("Successfully displayed uploaded image via /show API: %s", filename)
                        else:
                            response_text = await response.text()
                            await self._add_log(f"Failed to show uploaded image: {response.status} - {response_text}", "error")
                            _LOGGER.error("Failed to show uploaded image: %s - %s", response.status, response_text)

            # Refresh device info
            await self.async_update()

        except Exception as err:
            await self._add_log(f"Error playing media: {str(err)}", "error")
            _LOGGER.error("Error playing media: %s", str(err))

    async def async_browse_media(self, media_content_type: str | None = None, media_content_id: str | None = None) -> BrowseMedia:
        """Browse media - show device galleries and local media."""
        try:
            if media_content_id is None:
                # Root level - show both device galleries and local media
                return await self._browse_root()
            elif media_content_id == "device_galleries":
                # Show device galleries
                return await self._browse_galleries()
            elif media_content_id.startswith("gallery:"):
                # Browse specific gallery
                gallery_name = media_content_id[8:]  # Remove "gallery:" prefix
                return await self._browse_gallery_images(gallery_name)
            elif media_content_id == "local_media":
                # Browse local media using media_source
                return await media_source.async_browse_media(
                    self.hass,
                    None,
                    content_filter=lambda item: item.media_content_type.startswith('image/')
                )
            else:
                # Handle media source browsing
                return await media_source.async_browse_media(
                    self.hass,
                    media_content_id,
                    content_filter=lambda item: item.media_content_type.startswith('image/')
                )
        except Exception as err:
            _LOGGER.error("Error browsing media: %s", str(err))
            # Return empty browse media as fallback
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
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://{self._host}/gallery/list") as response:
                    if response.status == 200:
                        galleries_data = await response.json()
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
        except Exception as err:
            _LOGGER.error("Error browsing galleries: %s", str(err))
        
        # Return empty result on error
        return BrowseMedia(
            title="Device Galleries",
            media_class=MediaClass.DIRECTORY,
            media_content_type="directory",
            media_content_id="device_galleries",
            can_play=False,
            can_expand=False,
            children=[],
        )

    async def _browse_gallery_images(self, gallery_name: str) -> BrowseMedia:
        """Browse images in a specific gallery."""
        try:
            async with aiohttp.ClientSession() as session:
                # Get images from gallery
                params = {
                    "gallery_name": gallery_name,
                    "offset": 0,
                    "limit": 100  # Get up to 100 images
                }
                async with session.get(f"http://{self._host}/gallery", params=params) as response:
                    if response.status == 200:
                        gallery_data = await response.json()
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
        except Exception as err:
            _LOGGER.error("Error browsing gallery %s: %s", gallery_name, str(err))
        
        # Return empty result on error
        return BrowseMedia(
            title=f"Gallery: {gallery_name}",
            media_class=MediaClass.DIRECTORY,
            media_content_type="directory",
            media_content_id=f"gallery:{gallery_name}",
            can_play=False,
            can_expand=False,
            children=[],
        )

    async def _add_log(self, message: str, level: str = "info") -> None:
        """Add log entry."""
        from datetime import datetime
        
        log_entry = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        }
        
        if (DOMAIN in self.hass.data and 
            self._config_entry.entry_id in self.hass.data[DOMAIN]):
            
            logs = self.hass.data[DOMAIN][self._config_entry.entry_id].get("logs", [])
            logs.append(log_entry)
            # Keep only recent 50 logs
            if len(logs) > 50:
                logs.pop(0)
            self.hass.data[DOMAIN][self._config_entry.entry_id]["logs"] = logs