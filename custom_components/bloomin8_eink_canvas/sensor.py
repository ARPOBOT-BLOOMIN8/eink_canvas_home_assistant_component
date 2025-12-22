"""Support for BLOOMIN8 E-Ink Canvas sensors."""
from __future__ import annotations

import logging

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.entity import DeviceInfo

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
    """Set up the BLOOMIN8 E-Ink Canvas sensors."""
    host = config_entry.data[CONF_HOST]
    name = config_entry.data.get(CONF_NAME, DEFAULT_NAME)

    sensors = [
        EinkDeviceInfoSensor(hass, config_entry, host, name),
        EinkBatterySensor(hass, config_entry, host, name),
        EinkStorageSensor(hass, config_entry, host, name),
        EinkCurrentImageSensor(hass, config_entry, host, name),
        EinkLogSensor(hass, config_entry, host, name),
        EinkFirmwareVersionSensor(hass, config_entry, host, name),
        EinkWifiSSIDSensor(hass, config_entry, host, name),
        EinkScreenResolutionSensor(hass, config_entry, host, name),
    ]

    async_add_entities(sensors, True)


class EinkBaseSensor(SensorEntity):
    """Base class for BLOOMIN8 E-Ink Canvas sensors."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        self.hass = hass
        self._config_entry = config_entry
        self._host = host
        self._device_name = device_name
        self._attr_has_entity_name = True

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

    def _get_device_info(self) -> dict | None:
        """Get device info from shared runtime data."""
        runtime_data = self._config_entry.runtime_data
        return runtime_data.device_info

    async def _fetch_device_info(self) -> dict | None:
        """Fetch device info directly from device using API client."""
        runtime_data = self._config_entry.runtime_data
        api_client = runtime_data.api_client

        device_info = await api_client.get_device_info()
        if device_info:
            # Update shared runtime data
            runtime_data.device_info = device_info
        return device_info


class EinkDeviceInfoSensor(EinkBaseSensor):
    """Device information sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Device Info"
        self._attr_unique_id = f"eink_display_{host}_device_info"
        self._attr_icon = "mdi:information"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()
        
        if device_info:
            self._attr_native_value = "Online"
            self._attr_extra_state_attributes = {
                "device_name": device_info.get("name"),
                "firmware_version": device_info.get("version"),
                "board_model": device_info.get("board_model"),
                "screen_model": device_info.get("screen_model"),
                "network_type": device_info.get("network_type"),
                "wifi_ssid": device_info.get("sta_ssid"),
                "ip_address": device_info.get("sta_ip"),
                "resolution": f"{device_info.get('width', 0)}x{device_info.get('height', 0)}",
                "screen_width": device_info.get("width", 0),
                "screen_height": device_info.get("height", 0),
                "sleep_duration": device_info.get("sleep_duration"),
                "max_idle": device_info.get("max_idle"),
                "gallery": device_info.get("gallery"),
                "playlist": device_info.get("playlist"),
                "play_type": device_info.get("play_type"),
            }
        else:
            self._attr_native_value = "Offline"
            self._attr_extra_state_attributes = {}


class EinkBatterySensor(EinkBaseSensor):
    """Battery level sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Battery"
        self._attr_unique_id = f"eink_display_{host}_battery"
        self._attr_device_class = SensorDeviceClass.BATTERY
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_native_unit_of_measurement = PERCENTAGE
        self._attr_icon = "mdi:battery"

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()
        
        if device_info:
            self._attr_native_value = device_info.get("battery", 0)
        else:
            self._attr_native_value = None


class EinkStorageSensor(EinkBaseSensor):
    """Storage usage sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Storage"
        self._attr_unique_id = f"eink_display_{host}_storage"
        self._attr_icon = "mdi:harddisk"

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()
        
        if device_info:
            total_size = device_info.get("total_size", 0)
            free_size = device_info.get("free_size", 0)
            used_size = total_size - free_size
            
            if total_size > 0:
                usage_percent = round((used_size / total_size) * 100, 1)
                
                # Convert bytes to appropriate units for display
                def format_bytes(bytes_val):
                    if bytes_val >= 1024**3:  # GB
                        return f"{round(bytes_val / (1024**3), 2)} GB"
                    elif bytes_val >= 1024**2:  # MB
                        return f"{round(bytes_val / (1024**2), 1)} MB"
                    elif bytes_val >= 1024:  # KB
                        return f"{round(bytes_val / 1024, 1)} KB"
                    else:
                        return f"{bytes_val} B"
                
                used_formatted = format_bytes(used_size)
                total_formatted = format_bytes(total_size)
                
                # Display format: "85.2% (1.2 GB / 1.4 GB)"
                self._attr_native_value = f"{usage_percent}% ({used_formatted} / {total_formatted})"
                
                self._attr_extra_state_attributes = {
                    "usage_percentage": usage_percent,
                    "used_size_bytes": used_size,
                    "total_size_bytes": total_size,
                    "free_size_bytes": free_size,
                    "used_formatted": used_formatted,
                    "total_formatted": total_formatted,
                    "free_formatted": format_bytes(free_size),
                    "fs_ready": device_info.get("fs_ready", False),
                    "storage_status": "healthy" if usage_percent < 90 else "warning" if usage_percent < 95 else "critical",
                }
            else:
                self._attr_native_value = "Unknown"
                self._attr_extra_state_attributes = {}
        else:
            self._attr_native_value = "Offline"
            self._attr_extra_state_attributes = {}


class EinkCurrentImageSensor(EinkBaseSensor):
    """Current image sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Current Image"
        self._attr_unique_id = f"eink_display_{host}_current_image"
        self._attr_icon = "mdi:image"

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()
        
        if device_info and device_info.get("image"):
            image_path = device_info.get("image", "")
            image_name = image_path.split("/")[-1] if "/" in image_path else image_path
            self._attr_native_value = image_name
            self._attr_extra_state_attributes = {
                "full_path": image_path,
                "image_url": f"http://{self._host}{image_path}",
                "next_time": device_info.get("next_time"),
            }
        else:
            self._attr_native_value = "None"
            self._attr_extra_state_attributes = {}


class EinkLogSensor(EinkBaseSensor):
    """Log sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Logs"
        self._attr_unique_id = f"eink_display_{host}_logs"
        self._attr_icon = "mdi:text-box"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_update(self) -> None:
        """Update sensor state."""
        runtime_data = self._config_entry.runtime_data
        logs = runtime_data.logs

        if logs:
            latest_log = logs[-1]
            self._attr_native_value = latest_log["message"]

            # Show recent 10 logs in attributes
            recent_logs = logs[-10:] if len(logs) > 10 else logs
            log_history = []
            for log in recent_logs:
                timestamp = log["timestamp"].strftime("%H:%M:%S")
                log_history.append(f"[{timestamp}] {log['level'].upper()}: {log['message']}")

            self._attr_extra_state_attributes = {
                "latest_level": latest_log["level"],
                "latest_timestamp": latest_log["timestamp"].isoformat(),
                "total_logs": len(logs),
                "recent_logs": log_history,
            }
        else:
            self._attr_native_value = "No logs"
            self._attr_extra_state_attributes = {}


class EinkFirmwareVersionSensor(EinkBaseSensor):
    """Firmware version sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Firmware Version"
        self._attr_unique_id = f"eink_display_{host}_firmware_version"
        self._attr_icon = "mdi:chip"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()
        
        if device_info:
            self._attr_native_value = device_info.get("version", "Unknown")
        else:
            self._attr_native_value = "Offline"


class EinkWifiSSIDSensor(EinkBaseSensor):
    """WiFi SSID sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "WiFi SSID"
        self._attr_unique_id = f"eink_display_{host}_wifi_ssid"
        self._attr_icon = "mdi:wifi"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()

        if device_info:
            self._attr_native_value = device_info.get("sta_ssid", "Unknown")
            self._attr_extra_state_attributes = {
                "ip_address": device_info.get("sta_ip"),
                "network_type": device_info.get("network_type"),
            }
        else:
            self._attr_native_value = "Offline"
            self._attr_extra_state_attributes = {}


class EinkScreenResolutionSensor(EinkBaseSensor):
    """Screen resolution sensor."""

    def __init__(self, hass: HomeAssistant, config_entry: ConfigEntry, host: str, device_name: str) -> None:
        """Initialize the sensor."""
        super().__init__(hass, config_entry, host, device_name)
        self._attr_name = "Screen Resolution"
        self._attr_unique_id = f"eink_display_{host}_screen_resolution"
        self._attr_icon = "mdi:monitor-screenshot"
        self._attr_entity_category = EntityCategory.DIAGNOSTIC

    async def async_update(self) -> None:
        """Update sensor state."""
        device_info = self._get_device_info()
        if not device_info:
            device_info = await self._fetch_device_info()

        if device_info:
            width = device_info.get("width", 0)
            height = device_info.get("height", 0)

            # Determine canvas model based on resolution
            canvas_model = "Unknown"
            if width == 480 and height == 800:
                canvas_model = "7.3\" Canvas"
            elif width == 1200 and height == 1600:
                canvas_model = "13.3\" Canvas"
            elif width == 2160 and height == 3060:
                canvas_model = "28.5\" Canvas"

            self._attr_native_value = f"{width}x{height}"
            self._attr_extra_state_attributes = {
                "width": width,
                "height": height,
                "canvas_model": canvas_model,
                "screen_model": device_info.get("screen_model", "Unknown"),
                "aspect_ratio": f"{width}:{height}",
            }
        else:
            self._attr_native_value = "Unknown"
            self._attr_extra_state_attributes = {} 