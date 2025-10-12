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

# Error messages
ERROR_CANNOT_CONNECT = "cannot_connect"
ERROR_INVALID_AUTH = "invalid_auth"
ERROR_UNKNOWN = "unknown"
