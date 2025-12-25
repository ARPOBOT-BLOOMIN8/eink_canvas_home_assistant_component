"""Media source platform for BLOOMIN8 E-Ink Canvas."""

import logging

from homeassistant.components.media_source import MediaSource, MediaSourceItem, PlayMedia
from homeassistant.components.media_source import local_source
from homeassistant.components import media_source as ha_media_source
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

        except Unresolvable:
            # Expected validation errors should not spam the log.
            raise
        except Exception as err:
            _LOGGER.exception("Error resolving media source item")
            raise Unresolvable(str(err)) from err

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse media."""
        # Delegate browsing to HA's built-in media source implementations.
        # The previous approach used `local_source.LocalSource(...)` which is no longer
        # compatible with newer Home Assistant versions.
        try:
            # Prefer local_source module helper if present.
            if hasattr(local_source, "async_browse_media"):
                return await local_source.async_browse_media(
                    self.hass,
                    item.identifier or "",
                    content_filter=None,
                )

            # Fallback: ask the media_source component to generate an ID for the local source.
            if hasattr(ha_media_source, "generate_media_source_id"):
                local_root = ha_media_source.generate_media_source_id(local_source.DOMAIN)
                media_content_id = item.identifier or local_root
                return await ha_media_source.async_browse_media(
                    self.hass,
                    media_content_id,
                    content_filter=None,
                )

            raise Unresolvable(
                "Browsing local media is not available in this Home Assistant version."
            )
        except Unresolvable:
            raise
        except Exception as e:
            _LOGGER.exception("Error browsing local media")
            raise Unresolvable(
                "Unable to browse local media. Please make sure your media directory is configured in Home Assistant."
            ) from e
