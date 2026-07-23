# TODO / Future Improvements

This file tracks planned improvements and technical debt for the BLOOMIN8 E-Ink Canvas integration.

## ~~High Priority~~ Resolved

### ~~Use `async_config_entry_first_refresh()` for robust startup~~ ✅ REVIEWED (Won't Implement)
- ~~**Issue**: Integration currently starts even if the first device info fetch fails.~~
- **Resolution**: For battery-powered deep-sleep devices, this would be **harmful**:
  1. Device is expected to be offline at HA startup (sleeping for hours/days)
  2. `async_config_entry_first_refresh()` would raise `ConfigEntryNotReady` → integration fails to load
  3. User would see "Failed to set up" errors constantly until device wakes
- **Current approach**: Integration loads with cached data (persistent cache implemented). Entities show last known values. Device Info sensor shows "Offline". This is the correct UX for deep-sleep devices.

## ~~Medium Priority~~ Resolved

### ~~Make backoff cap configurable via Config Flow~~ ✅ OBSOLETE
- ~~**Issue**: The offline backoff max interval (currently 30 minutes) is hardcoded.~~
- **Resolution**: Polling has been removed entirely (`update_interval=None`). The integration now uses a push-only model. There is no backoff mechanism because there is no polling to back off from.

### ~~Add `always_update=False` to coordinator~~ ✅ DONE
- ~~**Issue**: Coordinator notifies all entities on every update, even if data hasn't changed.~~
- ~~**Improvement**: Set `always_update=False` to reduce unnecessary entity updates.~~
- ~~**Reference**: https://developers.home-assistant.io/docs/integration_fetching_data/~~

## Low Priority

### Verify and optionally require Manufacturer Data prefix `0x0085` for BLE matching
- **Context**: BLE advertisements observed across multiple displays include manufacturer data for company ID `0x013F` (decimal `319`) with payloads that often start with `00 85` (e.g. `0085d1648fc1`).
- **Why**: Using only the company ID could (in theory) match unrelated devices that share the same company identifier. If the `0x0085` prefix is a stable Bloomin8-specific marker, requiring it would reduce false positives.
- **What to do**:
  1. Collect additional BLE advertisement samples across different firmware/hardware revisions.
  2. Confirm whether the manufacturer payload for company ID `0x013F` always starts with `0x00 0x85`.
  3. If confirmed, update BLE detection to require either:
     - known service UUID (`0xFFF0` / `0xF000`) **or**
     - manufacturer company ID `0x013F` **and** payload prefix `0x0085`.
- **Acceptance criteria**:
  - Existing devices continue to be discovered.
  - No discovery regressions for devices that advertise only `FFF0` without manufacturer data.
  - Reduced chance of false positives when filtering the BLE device dropdown / bluetooth discovery step.

### ~~Consolidate refresh logic after actions~~ ✅ REVIEWED (No Change Needed)
- ~~**Issue**: Some services use `coordinator.async_set_updated_data()`, others use `async_request_refresh()`.~~
- **Resolution**: The two approaches serve different purposes and are already used correctly:
  - `async_request_refresh()`: Best-effort refresh after simple actions (show_next, clear_screen, whistle). Device may sleep quickly.
  - `async_set_updated_data()`: Push known data directly when we already have it (update_settings, refresh_device_info, upload after fetching deviceInfo).

### ~~Consider `UpdateFailed` exception instead of returning `None`~~ ✅ REVIEWED (Keep Current)
- ~~**Issue**: Coordinator returns `None` when device is offline instead of raising `UpdateFailed`.~~
- **Resolution**: For deep-sleep battery devices, `UpdateFailed` would cause:
  1. ERROR log spam (device is expected to be offline most of the time)
  2. Unnecessary retry loops that could drain battery if device wakes briefly
  3. Entities becoming "unavailable" instead of showing cached values
- **Current approach**: Return `None` → entities show last cached value, no error spam. This is the correct pattern for battery-powered devices that sleep for hours/days.
