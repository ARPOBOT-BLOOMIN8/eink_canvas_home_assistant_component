"""Media source platform for BLOOMIN8 E-Ink Canvas.

This platform exposes Canvas galleries as a Home Assistant Media Source provider.

Notes / trade-offs:
- Browse is user-driven and may trigger HTTP calls to the device (to list galleries).
- Resolved media URLs point to the device's local IP. This means remote access
  to the *image bytes* depends on the network; however, using the images as
  *input* for the Canvas (via this integration's Media Player) still works
  because Home Assistant fetches the bytes locally.
"""

from __future__ import annotations

import logging
from urllib.parse import quote, unquote

from homeassistant.components.media_player import MediaClass, MediaType
from homeassistant.components.media_source import (
	BrowseMediaSource,
	MediaSource,
	MediaSourceError,
	MediaSourceItem,
	PlayMedia,
)
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant

from .const import DEFAULT_NAME, DOMAIN

_LOGGER = logging.getLogger(__name__)

_SEG_DEVICE = "device"
_SEG_GALLERY = "gallery"
_SEG_IMAGE = "image"


def _enc(value: str) -> str:
	return quote(value or "", safe="")


def _dec(value: str) -> str:
	return unquote(value or "")


def _guess_mime_type(path: str) -> str:
	lower = (path or "").lower()
	if lower.endswith(".png"):
		return "image/png"
	if lower.endswith(".gif"):
		return "image/gif"
	if lower.endswith(".bmp"):
		return "image/bmp"
	if lower.endswith(".webp"):
		return "image/webp"
	return "image/jpeg"


class Bloomin8EinkCanvasMediaSource(MediaSource):
	"""Media source provider exposing Canvas galleries."""

	name = "BLOOMIN8 E-Ink Canvas"

	def __init__(self, hass: HomeAssistant) -> None:
		super().__init__(DOMAIN)
		self._hass = hass

	def _loaded_entry_ids(self) -> list[str]:
		domain_data = self._hass.data.get(DOMAIN, {})
		if not isinstance(domain_data, dict):
			return []
		return list(domain_data.keys())

	def _entry_title(self, entry_id: str) -> str:
		entry = self._hass.config_entries.async_get_entry(entry_id)
		if entry is None:
			return entry_id
		host = entry.data.get(CONF_HOST, "")
		name = entry.data.get(CONF_NAME) or host or DEFAULT_NAME

		# Prefer cached device name if available.
		runtime_data = self._hass.data.get(DOMAIN, {}).get(entry_id)
		device_name = None
		if runtime_data is not None:
			device_info = getattr(runtime_data, "device_info", None)
			if isinstance(device_info, dict):
				device_name = device_info.get("name")
		return str(device_name or name)

	def _get_runtime_data(self, entry_id: str):
		domain_data = self._hass.data.get(DOMAIN, {})
		if not isinstance(domain_data, dict) or entry_id not in domain_data:
			raise MediaSourceError(f"Unknown device entry_id: {entry_id}")
		return domain_data[entry_id]

	def _get_host(self, entry_id: str) -> str:
		entry = self._hass.config_entries.async_get_entry(entry_id)
		if entry is None:
			raise MediaSourceError(f"Unknown device entry_id: {entry_id}")
		host = entry.data.get(CONF_HOST)
		if not isinstance(host, str) or not host:
			raise MediaSourceError(f"Missing host for device entry_id: {entry_id}")
		return host

	@staticmethod
	def _parse_identifier(identifier: str) -> list[str]:
		# Identifier is a slash-separated path. We keep decoding until leaf parts.
		parts = [p for p in (identifier or "").split("/") if p]
		return parts

	async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
		"""Browse media for this provider."""
		identifier = item.identifier or ""
		parts = self._parse_identifier(identifier)

		# Provider root: list devices
		if not parts:
			base = BrowseMediaSource(
				domain=DOMAIN,
				identifier=None,
				title=self.name or DOMAIN,
				media_class=MediaClass.DIRECTORY,
				media_content_type="directory",
				can_play=False,
				can_expand=True,
			)

			base.children = [
				BrowseMediaSource(
					domain=DOMAIN,
					identifier=f"{_SEG_DEVICE}/{entry_id}",
					title=self._entry_title(entry_id),
					media_class=MediaClass.DIRECTORY,
					media_content_type="directory",
					can_play=False,
					can_expand=True,
				)
				for entry_id in sorted(self._loaded_entry_ids())
			]
			return base

		# device/<entry_id>
		if len(parts) == 2 and parts[0] == _SEG_DEVICE:
			entry_id = parts[1]
			runtime_data = self._get_runtime_data(entry_id)
			api_client = getattr(runtime_data, "api_client", None)
			if api_client is None:
				raise MediaSourceError(f"Device runtime not ready for entry_id: {entry_id}")

			galleries = await api_client.get_galleries()
			if not isinstance(galleries, list):
				galleries = []

			base = BrowseMediaSource(
				domain=DOMAIN,
				identifier=f"{_SEG_DEVICE}/{entry_id}",
				title=self._entry_title(entry_id),
				media_class=MediaClass.DIRECTORY,
				media_content_type="directory",
				can_play=False,
				can_expand=True,
			)

			children: list[BrowseMediaSource] = []
			for gallery in galleries:
				if not isinstance(gallery, dict):
					continue
				name = gallery.get("name")
				if not isinstance(name, str) or not name:
					continue
				children.append(
					BrowseMediaSource(
						domain=DOMAIN,
						identifier=f"{_SEG_DEVICE}/{entry_id}/{_SEG_GALLERY}/{_enc(name)}",
						title=name,
						media_class=MediaClass.DIRECTORY,
						media_content_type="directory",
						can_play=False,
						can_expand=True,
					)
				)

			base.children = sorted(children, key=lambda c: c.title)
			return base

		# device/<entry_id>/gallery/<gallery_name>
		if len(parts) == 4 and parts[0] == _SEG_DEVICE and parts[2] == _SEG_GALLERY:
			entry_id = parts[1]
			gallery_name = _dec(parts[3])

			runtime_data = self._get_runtime_data(entry_id)
			api_client = getattr(runtime_data, "api_client", None)
			if api_client is None:
				raise MediaSourceError(f"Device runtime not ready for entry_id: {entry_id}")

			gallery_data = await api_client.get_gallery_images(gallery_name)
			images = []
			if isinstance(gallery_data, dict):
				images = gallery_data.get("data") or []
			if not isinstance(images, list):
				images = []

			base = BrowseMediaSource(
				domain=DOMAIN,
				identifier=f"{_SEG_DEVICE}/{entry_id}/{_SEG_GALLERY}/{_enc(gallery_name)}",
				title=f"{self._entry_title(entry_id)} / {gallery_name}",
				media_class=MediaClass.DIRECTORY,
				media_content_type="directory",
				can_play=False,
				can_expand=True,
				children_media_class=MediaClass.IMAGE,
			)

			children: list[BrowseMediaSource] = []
			for image in images:
				if not isinstance(image, dict):
					continue
				filename = image.get("name")
				if not isinstance(filename, str) or not filename:
					continue
				children.append(
					BrowseMediaSource(
						domain=DOMAIN,
						identifier=(
							f"{_SEG_DEVICE}/{entry_id}/{_SEG_GALLERY}/{_enc(gallery_name)}/"
							f"{_SEG_IMAGE}/{_enc(filename)}"
						),
						title=filename,
						media_class=MediaClass.IMAGE,
						media_content_type=MediaType.IMAGE,
						can_play=True,
						can_expand=False,
					)
				)

			base.children = sorted(children, key=lambda c: c.title)
			return base

		raise MediaSourceError(f"Unsupported media identifier: {identifier}")

	async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
		"""Resolve a media item to a playable URL."""
		identifier = item.identifier or ""
		parts = self._parse_identifier(identifier)

		# device/<entry_id>/gallery/<gallery_name>/image/<filename>
		if not (
			len(parts) == 6
			and parts[0] == _SEG_DEVICE
			and parts[2] == _SEG_GALLERY
			and parts[4] == _SEG_IMAGE
		):
			raise MediaSourceError(f"Item is not resolvable: {identifier}")

		entry_id = parts[1]
		gallery_name = _dec(parts[3])
		filename = _dec(parts[5])

		if not gallery_name or not filename:
			raise MediaSourceError("Invalid gallery/image name")
		if ".." in gallery_name or ".." in filename:
			raise MediaSourceError("Invalid path")

		host = self._get_host(entry_id)
		image_path = f"/gallerys/{gallery_name}/{filename}"
		url = f"http://{host}{image_path}"

		return PlayMedia(url=url, mime_type=_guess_mime_type(filename))


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
	"""Set up the media_source provider."""
	_LOGGER.debug("Loaded BLOOMIN8 E-Ink Canvas media_source provider")
	return Bloomin8EinkCanvasMediaSource(hass)

