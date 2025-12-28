# Changelog

All notable changes to this project will be documented in this file.

The format is based on **[Keep a Changelog](https://keepachangelog.com/en/1.1.0/)**.
This project adheres to **[Semantic Versioning](https://semver.org/)**.

## [1.7.0] - Unreleased

### Added
- **Persistent device info cache**: After a Home Assistant restart, cached sensor values (battery level, storage, etc.) are immediately available — even if the device is asleep. Previously, all sensors showed "unavailable" until the device woke up.
- New diagnostic sensor **"Last Update"**: Shows the datetime of the last successful device info fetch. Useful for battery-powered devices that are often offline/asleep.
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
- BLE device selection dropdown in the config flow (uses Home Assistant Bluetooth discoveries).
- Bluetooth discovery flow support (`async_step_bluetooth`) to prefill the BLE address when the device is discovered.
- Discovery UX: show the discovered Bluetooth MAC address in Home Assistant's "Discovered" device list.
- Optional Bluetooth wake button (BLE) when a Bluetooth MAC address is configured (Sourced from https://github.com/mistrsoft/bloomin8_bt_wake)
- Automatic device info refresh after BLE wake button press (with short 5s timeout).
- Automatic device info refresh after state-changing actions (`show_next`, `clear_screen`, `whistle`, `upload_images_multi`, `upload_dithered_image_data`, `update_settings`, `show_playlist`).

### Changed
- Coordinator uses `always_update=False` to reduce unnecessary entity updates when data hasn't changed.
- Entities (sensors, selects, text) now show the last known cached value when the device is offline/asleep instead of becoming "unavailable". This provides a better user experience for battery-powered devices that sleep for hours or days.
- When `ble_auto_wake` is enabled and a BLE MAC is configured, the integration will attempt a best-effort BLE wake and wait for the device to come online before sending HTTP commands.
- Wake behavior is now centralized and supports an "auto" mode: `wake=None` means "only wake when BLE auto-wake is enabled and a MAC is configured" (battery-safe by default, reliable for UI actions when explicitly enabled).
- Default behavior now avoids periodic `/deviceInfo` polling so the Canvas can sleep.
- When polling is disabled, entities update from cached runtime data after a manual refresh (service/button).
- Config flow: if a BLE address is configured, send a BLE wake signal and wait ~10 seconds before validating the IP connection.
- `manifest.json`: declare Bluetooth discovery matcher for Bloomin8 service UUID.
- Internals: centralize `/deviceInfo` fetching via a shared `DataUpdateCoordinator` to avoid one HTTP call per entity.
- Internals: sensors and the media player consume coordinator snapshots; action-driven operations can push a fresh snapshot to update entities immediately.
- Media Player: album art / current image is now served via Home Assistant's media player proxy (and cached briefly in-memory) so dashboards/clients don't repeatedly fetch the image directly from the device and prevent auto-sleep.
- Internals: add an API helper to fetch raw image bytes (`get_image_bytes`) used by the media player proxy path.
- Diagnostic "Device Info" sensor no longer performs network I/O; it infers "Online" vs "Asleep (assumed)" from the age of the last successful snapshot vs `max_idle`.
- Reachability checks no longer use HTTP requests (which can reset `max_idle`); a short TCP connect probe is used instead to avoid keeping the device awake.
- Media Player: removed TURN_ON/TURN_OFF capability so dashboards no longer show a misleading "Off" control.

### Fixed
- Translation file consistency/valid JSON for new config field labels.
- `sensor.bloomin8_e_ink_canvas_last_update` is now restored across Home Assistant restarts (falls back to HA's restore state if the coordinator cache has no timestamp yet).
- Home Assistant thread-safety violations (callbacks now use thread-safe helpers like `schedule_update_ha_state` and `dispatcher_send`).
- External settings changes now reflect correctly after "Refresh Info": select/text entities force a refresh so updated `sleep_duration`, `max_idle`, and `idx_wake_sens` values are pulled from the latest cached `/deviceInfo` snapshot.
- `media_source` compatibility with newer Home Assistant versions (removed deprecated `local_source.LocalSource(...)` initialization).
- `media_source` browsing compatibility across Home Assistant versions (handle `generate_media_source_id()` signature differences).
- Media Player: accept Home Assistant's `MediaType.IMAGE` ("image") in content-type filtering (in addition to MIME types like `image/png`).
- Remove unused `media_source.py` provider to avoid load errors from Home Assistant API changes (media browsing is handled by the media player entity).
- Image upload robustness: use proper query `params` instead of manually building query strings (avoids HTTP/client parsing issues such as duplicate `Content-Length`).
- Work around device firmware returning invalid HTTP response headers (e.g., duplicate `Content-Length`) by falling back to a lenient raw-socket upload for `/upload`.
- BLE wake reliability: prefer `bleak_retry_connector.establish_connection` when available.
- BLE discovery/wake robustness across firmware variants: match additional BLE service UUID `0xFFF0` and try wake writes against both characteristic UUIDs (`0xF001` and `0xFFF1`); config flow BLE device filtering no longer relies on generic name substrings.
- BLE wake UX: hide BLE config fields and do not create the "Wake (Bluetooth)" button when Home Assistant has no Bluetooth / no connectable BLE proxy available.
- Reduce redundant `/deviceInfo` calls by sharing a single coordinator snapshot across entities.
- Increase `clear_screen` timeout from 10s to 30s (E-Ink display refresh takes ~15-20s).
- Polling: device offline/asleep now logs as INFO (throttled) instead of ERROR.
- Services now support device targeting via Home Assistant's `target` selector — when multiple Canvas devices are registered, you can specify which device(s) to control.
- Improve troubleshooting for sleep/battery issues: add targeted DEBUG logs when requests are skipped because the device is offline/asleep (wake=False), when media images are served from cache vs fetched, and BLE auto-wake connect/write/disconnect timing.
- BLE wake reliability: avoid potentially "sticky" BLE proxy connections by timing out response-required GATT writes and falling back to writes without response; add a disconnect timeout so the connection is released promptly.
- BLE wake reliability: send the wake signal as a short pulse (0x01 then 0x00), matching observed behavior. https://github.com/mistrsoft/bloomin8_bt_wake/issues/1#issuecomment-3694216426
- BLE wake button: post-wake refresh now retries and uses a wake-enabled `/deviceInfo` request to avoid being skipped while Wi‑Fi is still coming up.
- BLE wake UX: reduce noisy debug stack traces on the first post-wake `/deviceInfo` probe (common while Wi‑Fi/HTTP is still coming up), and avoid duplicate coordinator "Manually updated" logs by skipping identical snapshot pushes.
- Device Info sensor: after manual refresh / BLE wake updates pushed into the coordinator, the coordinator is now correctly marked as successful so "Asleep (assumed)" does not linger from a previous failed refresh.

## [1.6.0] 

### Added
- Initial release of the integration (HTTP/IP-based control, services, entities).

[1.7.0]: https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/compare/v1.6.0...HEAD
[1.6.0]: https://github.com/ARPOBOT-BLOOMIN8/eink_canvas_home_assistant_component/releases/tag/v1.6.0