"""Media source platform (deprecated).

Home Assistant automatically loads a `media_source` platform if this module is
present. Previous versions of this repository removed the actual provider logic
in favor of browsing via the media player entity.

However, keeping a file without the required entrypoint causes HA to crash on
startup with:
	AttributeError: module ... has no attribute 'async_get_media_source'

To stay backward compatible and keep HA stable, we expose a minimal provider
that deliberately does not offer any browsable/resolveable items.
"""

from __future__ import annotations

import logging

from homeassistant.components.media_source import MediaSource, MediaSourceError
from homeassistant.core import HomeAssistant

_LOGGER = logging.getLogger(__name__)


class _Bloomin8DeprecatedMediaSource(MediaSource):
	"""A minimal media source provider that is intentionally not usable."""

	name = "BLOOMIN8 E-Ink Canvas (deprecated)"

	async def async_resolve_media(self, item):  # type: ignore[override]
		raise MediaSourceError(
			"This integration does not provide a media_source provider. "
			"Use the Media Player entity's Browse Media instead."
		)

	async def async_browse_media(self, item):  # type: ignore[override]
		raise MediaSourceError(
			"This integration does not provide a media_source provider. "
			"Use the Media Player entity's Browse Media instead."
		)


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
	"""Set up the (deprecated) media_source provider.

	This is required so HA can import the platform without errors.
	"""
	_LOGGER.debug("Loaded deprecated media_source provider stub")
	return _Bloomin8DeprecatedMediaSource(hass)

