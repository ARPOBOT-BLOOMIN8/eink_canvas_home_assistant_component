"""The BLOOMIN8 E-Ink Canvas integration."""
from __future__ import annotations

from dataclasses import dataclass, field
import logging
import voluptuous as vol
from datetime import datetime
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform, CONF_HOST, CONF_NAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import device_registry as dr
import homeassistant.helpers.config_validation as cv

from .api_client import EinkCanvasApiClient
from .const import DOMAIN, DEFAULT_NAME

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


@dataclass
class RuntimeData:
    """Runtime data for BLOOMIN8 E-Ink Canvas integration."""

    api_client: EinkCanvasApiClient
    device_info: dict[str, Any] | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)


# Extend ConfigEntry to type hint runtime_data
type EinkCanvasConfigEntry = ConfigEntry[RuntimeData]


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

    # Create API client
    api_client = EinkCanvasApiClient(hass, host)

    # Store runtime data
    entry.runtime_data = RuntimeData(api_client=api_client)

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
        device_info = await api_client.get_device_info()
        if device_info:
            runtime_data.device_info = device_info
            add_log("Device info refreshed")
        else:
            add_log("Failed to refresh device info", "error")

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


async def async_unload_entry(hass: HomeAssistant, entry: EinkCanvasConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # Remove services
        services_to_remove = [
            "show_next", "sleep", "reboot", "clear_screen",
            "whistle", "refresh_device_info", "update_settings"
        ]
        for service in services_to_remove:
            if hass.services.has_service(DOMAIN, service):
                hass.services.async_remove(DOMAIN, service)

    return unload_ok
