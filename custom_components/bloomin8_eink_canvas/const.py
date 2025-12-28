"""Constants for the BLOOMIN8 E-Ink Canvas integration."""

# Domain identifier for the integration
DOMAIN = "bloomin8_eink_canvas"

# API Endpoints - System APIs
ENDPOINT_STATUS = "/state"  # Endpoint for checking device status
ENDPOINT_DEVICE_INFO = "/deviceInfo"  # Endpoint for retrieving device information
ENDPOINT_SHOW = "/show"  # Endpoint for displaying images
ENDPOINT_SHOW_NEXT = "/showNext"  # Endpoint for showing the next image
ENDPOINT_SLEEP = "/sleep"  # Endpoint for putting device to sleep
ENDPOINT_REBOOT = "/reboot"  # Endpoint for rebooting device
ENDPOINT_CLEAR_SCREEN = "/clearScreen"  # Endpoint for clearing screen
ENDPOINT_SETTINGS = "/settings"  # Endpoint for writing settings
ENDPOINT_WHISTLE = "/whistle"  # Endpoint for keep-alive

# API Endpoints - Image APIs
ENDPOINT_UPLOAD = "/upload"  # Endpoint for uploading images
ENDPOINT_UPLOAD_MULTI = "/image/uploadMulti"  # Endpoint for uploading multiple images
ENDPOINT_DATA_UPLOAD = "/image/dataUpload"  # Endpoint for uploading dithered image data
ENDPOINT_DELETE_IMAGE = "/image/delete"  # Endpoint for deleting images

# API Endpoints - Gallery APIs
ENDPOINT_GALLERY_LIST = "/gallery/list"  # Endpoint for listing all galleries
ENDPOINT_GALLERY = "/gallery"  # Endpoint for gallery operations (GET/PUT/DELETE)

# API Endpoints - Playlist APIs
ENDPOINT_PLAYLIST_LIST = "/playlist/list"  # Endpoint for listing all playlists
ENDPOINT_PLAYLIST = "/playlist"  # Endpoint for playlist operations (GET/PUT/DELETE)

# Default Values
DEFAULT_NAME = "BLOOMIN8 Canvas"  # Default name for the device

# Image Settings
# Note: Resolution is now detected dynamically from device info (width/height fields)
# Supported resolutions:
#   7.3" Canvas: 480x800
#   13.3" Canvas: 1200x1600
#   28.5" Canvas: 2160x3060
SUPPORTED_FORMATS = ["JPEG", "JPG", "PNG", "GIF", "BMP", "WEBP"]  # Supported input image formats that will be converted to JPEG

# Configuration
CONF_NAME = "name"
CONF_ORIENTATION = "orientation"
CONF_FILL_MODE = "fill_mode"
CONF_CONTAIN_COLOR = "contain_color"

# Optional Bluetooth wake configuration
CONF_MAC_ADDRESS = "mac_address"

# If enabled, the integration will try to wake the device via BLE (using mac_address)
# before sending HTTP API commands.
CONF_BLE_AUTO_WAKE = "ble_auto_wake"
DEFAULT_BLE_AUTO_WAKE = False

# Dispatcher signal base for notifying entities that cached runtime data changed.
# Entities use: f"{SIGNAL_DEVICE_INFO_UPDATED}_{entry_id}".
SIGNAL_DEVICE_INFO_UPDATED = f"{DOMAIN}_device_info_updated"

# Confirmed BLE details from reverse engineering (mistrsoft/bloomin8_bt_wake)
BLE_SERVICE_UUID = "0000f000-0000-1000-8000-00805f9b34fb"
BLE_CHAR_UUID = "0000f001-0000-1000-8000-00805f9b34fb"
# Newer reverse engineering indicates wake behaves like a short "pulse":
# write 0x01 (assert) and then 0x00 (release).
BLE_WAKE_PAYLOAD_ON = b"\x01"
BLE_WAKE_PAYLOAD_OFF = b"\x00"
BLE_WAKE_PULSE: tuple[bytes, bytes] = (BLE_WAKE_PAYLOAD_ON, BLE_WAKE_PAYLOAD_OFF)
# Small gap between the two writes; keep conservative and fast.
BLE_WAKE_PULSE_GAP_SECONDS = 0.05

# Backwards compatibility: older call sites may still import BLE_WAKE_PAYLOAD.
BLE_WAKE_PAYLOAD = BLE_WAKE_PAYLOAD_ON

# Some firmware variants advertise a different primary service (e.g. 0xFFF0) and
# use the corresponding wake characteristic (e.g. 0xFFF1). We support both to
# keep discovery and wake robust across device generations.
BLE_ALT_SERVICE_UUID = "0000fff0-0000-1000-8000-00805f9b34fb"
BLE_ALT_CHAR_UUID = "0000fff1-0000-1000-8000-00805f9b34fb"

# Manufacturer / company identifier observed in advertisements (little-endian in
# raw bytes). Example: 0x013F == 319.
BLE_MANUFACTURER_ID = 0x013F

# Candidates used for discovery and wake attempts.
BLE_SERVICE_UUIDS: tuple[str, ...] = (BLE_SERVICE_UUID, BLE_ALT_SERVICE_UUID)
BLE_WAKE_CHAR_UUIDS: tuple[str, ...] = (BLE_CHAR_UUID, BLE_ALT_CHAR_UUID)

# Image processing options
ORIENTATION_PORTRAIT = "portrait"
ORIENTATION_LANDSCAPE = "landscape"

FILL_MODE_CONTAIN = "contain"
FILL_MODE_COVER = "cover"
FILL_MODE_AUTO = "auto"

# Default image processing settings
DEFAULT_ORIENTATION = ORIENTATION_PORTRAIT
DEFAULT_FILL_MODE = FILL_MODE_AUTO
DEFAULT_CONTAIN_COLOR = "white"

# Background colors for contain mode (key must be lowercase alphanumeric for HA translations)
CONTAIN_COLORS = {
    "white": "#FFFFFF",
    "black": "#000000",
}

# Error messages
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_UNKNOWN = "unknown"

# Post-wake refresh timeout (shorter than normal to quickly detect if device woke up)
POST_WAKE_REFRESH_TIMEOUT_SECONDS = 5

# Service targeting attributes
ATTR_DEVICE_ID = "device_id"
ATTR_ENTITY_ID = "entity_id"

# List of all services (for registration/cleanup)
SERVICE_NAMES = [
    "show_next",
    "sleep",
    "reboot",
    "clear_screen",
    "whistle",
    "refresh_device_info",
    "update_settings",
    "upload_image_url",
    "upload_image_data",
    "upload_images_multi",
    "upload_dithered_image_data",
    "delete_image",
    "create_gallery",
    "delete_gallery",
    "list_galleries",
    "show_playlist",
    "list_playlists",
    "get_playlist",
    "put_playlist",
    "delete_playlist",
]
