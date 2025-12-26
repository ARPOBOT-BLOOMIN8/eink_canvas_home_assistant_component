# Changelog

All notable changes to this project will be documented in this file.

The format is based on **[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)**.
This project adheres to **[Semantic Versioning](https://semver.org/)**.

## [1.7.0] - Unreleased

### Added
- New "Sleep" button entity to put the device into sleep mode directly from the UI.
- New services to send images to the Canvas:
	- `upload_image_url` (download from URL, optional processing, upload, optional show_now)
	- `upload_image_data` (base64 image bytes, optional processing, upload, optional show_now)
	- `upload_dithered_image_data` (base64 raw dithered payload upload)
- New config option `ble_auto_wake` (default: off) to auto-wake the device via BLE before HTTP API calls.
- API client support for additional Image APIs:
	- `/image/uploadMulti`
	- `/image/dataUpload`
	- `/image/delete`

- New services to expose additional Bloomin8 API features:
	- `upload_images_multi` (batch upload via `/image/uploadMulti`)
	- `list_galleries` (list galleries via `/gallery/list`)
	- `list_playlists` (list playlists via `/playlist/list`)
	- `get_playlist` (fetch a playlist via `/playlist?name=...`)
- Config option `enable_polling` to control periodic HTTP polling.
- BLE device selection dropdown in the config flow (uses Home Assistant Bluetooth discoveries).
- Bluetooth discovery flow support (`async_step_bluetooth`) to prefill the BLE address when the device is discovered.
- Optional Bluetooth wake button (BLE) when a Bluetooth MAC address is configured.

### Changed
- When `ble_auto_wake` is enabled and a BLE MAC is configured, the integration will attempt a best-effort BLE wake and wait for the device to come online before sending HTTP commands.
- Default behavior now avoids periodic `/deviceInfo` polling so the Canvas can sleep.
- When polling is disabled, entities update from cached runtime data after a manual refresh (service/button).
- Config flow: if a BLE address is configured, send a BLE wake signal and wait ~10 seconds before validating the IP connection.
- `enable_polling` now uses a safe interval derived from the device's `max_idle` (polling interval is always > max_idle), so polling should not keep the device awake.
- Reduce log noise when the Canvas is asleep/offline: scheduled polling no longer spams ERROR logs for missing `/deviceInfo` responses.
- `manifest.json`: declare Bluetooth discovery matcher for Bloomin8 service UUID.
- Internals: centralize `/deviceInfo` fetching via a shared `DataUpdateCoordinator` to avoid one HTTP call per entity.
- Internals: sensors and the media player consume coordinator snapshots; action-driven operations can push a fresh snapshot to update entities immediately.

### Fixed
- Translation file consistency/valid JSON for new config field labels.
- Home Assistant thread-safety violations (callbacks now use thread-safe helpers like `schedule_update_ha_state` and `dispatcher_send`).
- `media_source` compatibility with newer Home Assistant versions (removed deprecated `local_source.LocalSource(...)` initialization).
- Image upload robustness: use proper query `params` instead of manually building query strings (avoids HTTP/client parsing issues such as duplicate `Content-Length`).
- Work around device firmware returning invalid HTTP response headers (e.g., duplicate `Content-Length`) by falling back to a lenient raw-socket upload for `/upload`.
- BLE wake reliability: prefer `bleak_retry_connector.establish_connection` when available.
- Reduce redundant `/deviceInfo` calls by sharing a single coordinator snapshot across entities.
- Increase `clear_screen` timeout from 10s to 30s (E-Ink display refresh takes ~15-20s).

## [1.6.0] 

### Added
- Initial release of the integration (HTTP/IP-based control, services, entities).

[1.7.0]: https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/releases/tag/v1.6.0