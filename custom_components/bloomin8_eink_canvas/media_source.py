"""Media source platform for BLOOMIN8 E-Ink Canvas."""
import aiohttp
import logging
from typing import Tuple
from PIL import Image
from io import BytesIO

from homeassistant.components.media_source import MediaSource, MediaSourceItem, PlayMedia
from homeassistant.components.media_source import local_source
from homeassistant.components.media_source.models import BrowseMediaSource
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry

from .const import (
    DOMAIN,
    ENDPOINT_UPLOAD,
    SUPPORTED_FORMATS,
)

_LOGGER = logging.getLogger(__name__)

async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Set up BLOOMIN8 E-Ink Canvas media source."""
    return EinkDisplayMediaSource(hass)

class EinkDisplayMediaSource(MediaSource):
    """Provide BLOOMIN8 E-Ink Canvas media items."""

    name: str = "BLOOMIN8 E-Ink Canvas"

    def __init__(self, hass: HomeAssistant):
        """Initialize EinkDisplay source."""
        super().__init__(DOMAIN)
        self.hass = hass
        self._local_source = local_source.LocalSource(hass)

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a media item to play."""
        try:
            # Get the actual file path for local files
            if hasattr(local_source, 'is_media_source_id') and local_source.is_media_source_id(item.identifier):
                play_item = await local_source.async_resolve_media(
                    self.hass, item.identifier, None
                )
                file_path = play_item.url
                if file_path.startswith("/"):
                    file_path = file_path[1:]  # Remove leading slash
            else:
                file_path = item.identifier

            # Check if the file is a supported format
            if not any(file_path.lower().endswith(f".{fmt.lower()}") for fmt in SUPPORTED_FORMATS):
                raise Unresolvable(f"Only {', '.join(SUPPORTED_FORMATS)} images are supported")

            # Return a custom URL scheme that our media player will handle
            return PlayMedia(f"eink-display://upload?file={file_path}", "image/jpg")

        except Exception as e:
            _LOGGER.error("Error handling media: %s", str(e))
            raise Unresolvable(str(e))

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse media."""
        # Directly browse local media for file selection
        try:
            return await self._local_source.async_browse_media(MediaSourceItem(
                hass=self.hass,
                domain=local_source.DOMAIN,
                identifier=item.identifier or "",
                target_media_player=item.target_media_player
            ))
        except Exception as e:
            _LOGGER.error("Error browsing local media: %s", str(e))
            raise Unresolvable("Unable to browse local media. Please make sure your media directory is configured in Home Assistant.")
