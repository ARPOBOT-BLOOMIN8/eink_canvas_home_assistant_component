"""The BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

import logging
import aiohttp
import async_timeout
import voluptuous as vol
from datetime import datetime

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import AddEntitiesCallback
import homeassistant.helpers.config_validation as cv

from .const import (
    DOMAIN,
    ENDPOINT_SHOW_NEXT,
    ENDPOINT_SLEEP,
    ENDPOINT_REBOOT,
    ENDPOINT_CLEAR_SCREEN,
    ENDPOINT_SETTINGS,
    ENDPOINT_WHISTLE,
    ENDPOINT_DEVICE_INFO,
    DEFAULT_NAME,
)

_LOGGER = logging.getLogger(__name__)

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
    hass.data.setdefault(DOMAIN, {})
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up BLOOMIN8 E-Ink Canvas from a config entry."""
    host = entry.data[CONF_HOST]
    name = entry.data.get(CONF_NAME, DEFAULT_NAME)
    
    # Store configuration data
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "host": host,
        "name": name,
        "device_info": None,
        "logs": [],
    }

    # Create device registration
    device_registry = dr.async_get(hass)
    device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, host)},
        name=name,
        manufacturer="BLOOMIN8",
        model="E-Ink Canvas",
        # configuration_url=f"http://{host}",  # Disabled to prevent external access
    )

    # Register services
    await _register_services(hass, entry)

    # Set up platforms
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    
    return True

async def _register_services(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Register device control services."""
    host = entry.data[CONF_HOST]
    
    async def add_log(message: str, level: str = "info") -> None:
        """Add log entry."""
        log_entry = {
            "timestamp": datetime.now(),
            "level": level,
            "message": message,
        }
        
        if DOMAIN in hass.data and entry.entry_id in hass.data[DOMAIN]:
            logs = hass.data[DOMAIN][entry.entry_id].get("logs", [])
            logs.append(log_entry)
            # Keep only the latest 50 logs
            if len(logs) > 50:
                logs.pop(0)
            hass.data[DOMAIN][entry.entry_id]["logs"] = logs

    async def handle_show_next(call: ServiceCall) -> None:
        """Handle show next image service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"http://{host}{ENDPOINT_SHOW_NEXT}") as response:
                        if response.status == 200:
                            await add_log("Successfully switched to next image")
                            _LOGGER.info("Successfully sent showNext command")
                        else:
                            await add_log(f"Failed to switch to next image: {response.status}", "error")
                            _LOGGER.error("ShowNext failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Error switching to next image: {str(err)}", "error")
            _LOGGER.error("Error in showNext service: %s", str(err))

    async def handle_sleep(call: ServiceCall) -> None:
        """Handle device sleep service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"http://{host}{ENDPOINT_SLEEP}") as response:
                        if response.status == 200:
                            await add_log("Device entered sleep mode")
                            _LOGGER.info("Device sleep command sent successfully")
                        else:
                            await add_log(f"Device sleep failed: {response.status}", "error")
                            _LOGGER.error("Sleep failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Device sleep error: {str(err)}", "error")
            _LOGGER.error("Error in sleep service: %s", str(err))

    async def handle_reboot(call: ServiceCall) -> None:
        """Handle device reboot service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"http://{host}{ENDPOINT_REBOOT}") as response:
                        if response.status == 200:
                            await add_log("Device reboot command sent")
                            _LOGGER.info("Device reboot command sent successfully")
                        else:
                            await add_log(f"Device reboot failed: {response.status}", "error")
                            _LOGGER.error("Reboot failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Device reboot error: {str(err)}", "error")
            _LOGGER.error("Error in reboot service: %s", str(err))

    async def handle_clear_screen(call: ServiceCall) -> None:
        """Handle clear screen service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(f"http://{host}{ENDPOINT_CLEAR_SCREEN}") as response:
                        if response.status == 200:
                            await add_log("Screen cleared")
                            _LOGGER.info("Screen cleared successfully")
                        else:
                            await add_log(f"Clear screen failed: {response.status}", "error")
                            _LOGGER.error("Clear screen failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Clear screen error: {str(err)}", "error")
            _LOGGER.error("Error in clear screen service: %s", str(err))

    async def handle_whistle(call: ServiceCall) -> None:
        """Handle keep alive service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://{host}{ENDPOINT_WHISTLE}") as response:
                        if response.status == 200:
                            await add_log("Keep alive signal sent")
                            _LOGGER.info("Whistle command sent successfully")
                        else:
                            await add_log(f"Keep alive failed: {response.status}", "error")
                            _LOGGER.error("Whistle failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Keep alive error: {str(err)}", "error")
            _LOGGER.error("Error in whistle service: %s", str(err))

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
            await add_log("No settings parameters provided", "warning")
            return

        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        f"http://{host}{ENDPOINT_SETTINGS}",
                        json=settings_data,
                        headers={"Content-Type": "application/json"}
                    ) as response:
                        if response.status == 200:
                            settings_str = ", ".join([f"{k}: {v}" for k, v in settings_data.items()])
                            await add_log(f"Device settings updated: {settings_str}")
                            _LOGGER.info("Settings updated successfully: %s", settings_data)
                        else:
                            await add_log(f"Settings update failed: {response.status}", "error")
                            _LOGGER.error("Settings update failed with status %s", response.status)
                        response.raise_for_status()
        except Exception as err:
            await add_log(f"Settings update error: {str(err)}", "error")
            _LOGGER.error("Error in update settings service: %s", str(err))

    async def handle_refresh_device_info(call: ServiceCall) -> None:
        """Handle refresh device info service."""
        try:
            async with async_timeout.timeout(10):
                async with aiohttp.ClientSession() as session:
                    async with session.get(f"http://{host}{ENDPOINT_DEVICE_INFO}") as response:
                        if response.status == 200:
                            # Check content type and handle accordingly
                            content_type = response.headers.get('content-type', '')
                            if 'application/json' in content_type:
                                device_info = await response.json()
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
                                        device_info = json.loads(json_str)
                                    else:
                                        device_info = None
                                except Exception as json_err:
                                    _LOGGER.error("Failed to parse device response: %s", json_err)
                                    device_info = None
                            
                            if device_info:
                                hass.data[DOMAIN][entry.entry_id]["device_info"] = device_info
                                await add_log("Device info refreshed")
                                _LOGGER.info("Device info refreshed successfully")
                            else:
                                await add_log("Failed to parse device info", "error")
                                _LOGGER.error("Failed to parse device info")
                        else:
                            await add_log(f"Refresh device info failed: {response.status}", "error")
                            _LOGGER.error("Refresh device info failed with status %s", response.status)
        except Exception as err:
            await add_log(f"Refresh device info error: {str(err)}", "error")
            _LOGGER.error("Error in refresh device info service: %s", str(err))

    # Register all services
    services = [
        ("show_next", handle_show_next, {}),
        ("sleep", handle_sleep, {}),
        ("reboot", handle_reboot, {}),
        ("clear_screen", handle_clear_screen, {}),
        ("whistle", handle_whistle, {}),
        ("refresh_device_info", handle_refresh_device_info, {}),
        ("update_settings", handle_update_settings, {
            vol.Optional("name"): str,
            vol.Optional("sleep_duration"): int,
            vol.Optional("max_idle"): int,
            vol.Optional("idx_wake_sens"): int,
        }),
    ]

    for service_name, handler, schema in services:
        hass.services.async_register(
            DOMAIN,
            service_name,
            handler,
            schema=vol.Schema(schema)
        )

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)
        
        # Remove services
        services_to_remove = [
            "show_next", "sleep", "reboot", "clear_screen", 
            "whistle", "refresh_device_info", "update_settings"
        ]
        for service in services_to_remove:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)
    
    return unload_ok
