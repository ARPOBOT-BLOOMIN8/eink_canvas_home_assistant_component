"""API Client for BLOOMIN8 E-Ink Canvas.

This client implements the official Bloomin8 E-Ink Canvas API as documented in openapi.yaml.
The device returns some responses with incorrect content-types (e.g., text/json, text/javascript
instead of application/json), so we handle JSON parsing manually where needed.
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import async_timeout

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
    ENDPOINT_STATUS,
)

_LOGGER = logging.getLogger(__name__)


class EinkCanvasApiClient:
    """API client for BLOOMIN8 E-Ink Canvas device."""

    def __init__(self, hass: HomeAssistant, host: str) -> None:
        """Initialize the API client."""
        self._hass = hass
        self._host = host
        self._session = async_get_clientsession(hass)

    @property
    def host(self) -> str:
        """Return the device host."""
        return self._host

    async def get_status(self) -> dict[str, Any] | None:
        """Get device status."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_STATUS}"
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
        except Exception as err:
            _LOGGER.debug("Error getting status: %s", err)
            return None

    async def get_device_info(self) -> dict[str, Any] | None:
        """Get device information from /deviceInfo endpoint.

        Returns device status including name, version, battery, screen resolution,
        current image, network info, etc. See openapi.yaml for full response schema.
        """
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_DEVICE_INFO}"
                ) as response:
                    if response.status == 200:
                        text_response = await response.text()
                        # Device may return incorrect content-type, parse JSON manually
                        try:
                            return json.loads(text_response)
                        except json.JSONDecodeError:
                            # Try to extract JSON from malformed response
                            start = text_response.find("{")
                            end = text_response.rfind("}") + 1
                            if start >= 0 and end > start:
                                return json.loads(text_response[start:end])
                            _LOGGER.warning("Invalid JSON in device info response")
                    return None
        except Exception as err:
            _LOGGER.debug("Error getting device info: %s", err)
            return None

    async def show_next(self) -> bool:
        """Show next image."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SHOW_NEXT}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Successfully sent showNext command")
                        return True
                    _LOGGER.error("ShowNext failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in showNext: %s", err)
            return False

    async def sleep(self) -> bool:
        """Put device to sleep."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SLEEP}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Device sleep command sent successfully")
                        return True
                    _LOGGER.error("Sleep failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in sleep: %s", err)
            return False

    async def reboot(self) -> bool:
        """Reboot device."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_REBOOT}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Device reboot command sent successfully")
                        return True
                    _LOGGER.error("Reboot failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in reboot: %s", err)
            return False

    async def clear_screen(self) -> bool:
        """Clear the screen."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_CLEAR_SCREEN}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Screen cleared successfully")
                        return True
                    _LOGGER.error("Clear screen failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in clear screen: %s", err)
            return False

    async def whistle(self) -> bool:
        """Send keep-alive signal."""
        try:
            async with async_timeout.timeout(10):
                async with self._session.get(
                    f"http://{self._host}{ENDPOINT_WHISTLE}"
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Whistle command sent successfully")
                        return True
                    _LOGGER.error("Whistle failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in whistle: %s", err)
            return False

    async def update_settings(self, settings: dict[str, Any]) -> bool:
        """Update device settings."""
        if not settings:
            _LOGGER.warning("No settings parameters provided")
            return False

        try:
            async with async_timeout.timeout(10):
                async with self._session.post(
                    f"http://{self._host}{ENDPOINT_SETTINGS}",
                    json=settings,
                    headers={"Content-Type": "application/json"}
                ) as response:
                    if response.status == 200:
                        _LOGGER.info("Settings updated successfully: %s", settings)
                        return True
                    _LOGGER.error("Settings update failed with status %s", response.status)
                    return False
        except Exception as err:
            _LOGGER.error("Error in update settings: %s", err)
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

            return await self.show_image_by_name(filename, gallery, play_type, dither, duration)
        except Exception as err:
            _LOGGER.error("Error showing image: %s", err)
            return False

    async def show_image_by_name(
        self,
        filename: str,
        gallery: str = "default",
        play_type: int = 0,
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
            show_data = {
                "play_type": play_type
            }

            if play_type == 0:
                # Single image mode: requires full path
                show_data["image"] = f"/gallerys/{gallery}/{filename}"
            elif play_type == 1:
                # Gallery slideshow mode: requires gallery, duration, and filename only
                show_data["image"] = filename
                show_data["gallery"] = gallery
                show_data["duration"] = duration
            elif play_type == 2:
                # Playlist mode: would need playlist parameter
                show_data["image"] = f"/gallerys/{gallery}/{filename}"

            if dither is not None:
                show_data["dither"] = dither

            _LOGGER.info("Showing image - gallery: %s, filename: %s, data: %s", gallery, filename, show_data)

            async with self._session.post(
                f"http://{self._host}{ENDPOINT_SHOW}",
                json=show_data
            ) as response:
                if response.status == 200:
                    _LOGGER.info("Successfully displayed image: %s/%s", gallery, filename)
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
        for attempt in range(max_retries):
            try:
                form = aiohttp.FormData()
                form.add_field(
                    "image",
                    image_data,
                    filename=filename,
                    content_type="image/jpeg"
                )

                # Build URL with query parameters as per original working code
                upload_url = f"http://{self._host}{ENDPOINT_UPLOAD}?filename={filename}&gallery={gallery}&show_now={'1' if show_now else '0'}"

                async with async_timeout.timeout(30):
                    async with self._session.post(
                        upload_url,
                        data=form
                    ) as response:
                        if response.status == 200:
                            response_text = await response.text()

                            try:
                                result = json.loads(response_text)
                                _LOGGER.info("Upload response: %s", result)
                                # Response contains directory path only, append filename
                                base_path = result.get("path", f"/gallerys/{gallery}/")
                                if not base_path.endswith("/"):
                                    base_path += "/"
                                image_path = f"{base_path}{filename}"
                                _LOGGER.info("Constructed path: %s (base: %s, filename: %s)",
                                           image_path, base_path, filename)
                                return image_path
                            except json.JSONDecodeError as e:
                                # Fallback to default path construction
                                _LOGGER.warning("Failed to parse upload response: %s", e)
                                image_path = f"/gallerys/{gallery}/{filename}"
                                _LOGGER.info("Using default path: %s", image_path)
                                return image_path

                        response_text = await response.text()
                        _LOGGER.error("Upload failed: %s - %s", response.status, response_text)
                        return None

            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as err:
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
                _LOGGER.error("Unexpected upload error: %s", err)
                return None

        return None

    async def get_galleries(self) -> list[dict[str, Any]]:
        """Get list of all galleries via /gallery/list endpoint.

        Returns:
            List of gallery objects with 'name' field, e.g., [{"name": "default"}]

        Note:
            Device returns content-type text/json instead of application/json.
        """
        try:
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
