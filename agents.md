# agents.md

This file is guidance for coding agents (and humans) working on this repository.

## Project context

- This repository contains a **Home Assistant custom integration** for **BLOOMIN8 E‑Ink Canvas**.
- Integration domain: `bloomin8_eink_canvas` (see `custom_components/bloomin8_eink_canvas/manifest.json`).
- Primary code lives in `custom_components/bloomin8_eink_canvas/`.
- The device is reached via **local IP** (config flow uses `CONF_HOST`).

## Golden rules for making changes

1. **Be minimal and targeted.** Prefer the smallest change that fixes the issue.
2. **Keep Home Assistant patterns.**
   - Use async I/O (`async def`, `await`) and avoid blocking calls in the event loop.
   - Follow HA entity platform conventions (sensor/button/select/text/media_player).
3. **Don’t break existing configs.**
   - Preserve config entry keys and defaults unless a migration is implemented.
   - If you must rename/replace an option, add a migration and document it.
4. **Log usefully, not noisily.**
   - Use `logging.getLogger(__name__)`.
   - Avoid spammy `INFO` logs in tight polling loops.
5. **Document user-facing changes.**
   - If behavior changes, update `README.md`, `services.yaml`, and/or translations.

## Repository map (important files)

- `custom_components/bloomin8_eink_canvas/api_client.py` — device API client (HTTP).
- `custom_components/bloomin8_eink_canvas/config_flow.py` — UI setup/reconfigure flow.
- `custom_components/bloomin8_eink_canvas/const.py` — domain/constants/options.
- `custom_components/bloomin8_eink_canvas/services.yaml` — service descriptions.
- `openapi.yaml` — API reference for the device endpoints.
- `docs/` — additional integration/API notes.

## Development & validation checklist

### Before you code

- Identify the correct layer:
  - Protocol/API issues → `api_client.py`
  - Entities and state updates → `sensor.py`, `media_player.py`, etc.
  - Options & UX → `config_flow.py`, `select.py`, `text.py`
  - Service wiring → `__init__.py` / platform files + `services.yaml`

### While coding

- Prefer **type hints** and clear error handling.
- Treat the device as unreliable:
  - Timeouts, connection errors, and empty responses should be handled gracefully.
  - Return `None` or raise a well-scoped exception, depending on caller expectations.
- Keep network calls centralized in the API client.

### After coding

- Run basic sanity checks locally:
  - Ensure the integration imports cleanly.
  - Ensure the config flow still works.
  - Ensure services still call the expected device endpoints.
- Update docs/strings if needed:
  - `services.yaml` for service schema/description changes.
  - `strings.json` and `translations/*.json` for UI text changes.

## Home Assistant specifics (do/do-not)

### Do

- Use HA helpers and conventions where present in the codebase.
- Keep entity unique IDs stable.
- Make sure option defaults align with `const.py`.

### Thread-safety (asyncio) — critical

Home Assistant enforces thread-safety rules (and may raise at runtime) when integrations call async APIs from a non-event-loop thread.

- Prefer `@callback` for lightweight callbacks that must run in the event loop.
- If a callback *might* run outside the event loop:
  - Use `self.schedule_update_ha_state(...)` instead of `self.async_write_ha_state()` / `self.async_schedule_update_ha_state(...)`.
  - Use `dispatcher_send(...)` instead of `async_dispatcher_send(...)`.
  - Use `hass.create_task(...)` instead of `hass.async_create_task(...)`.
- Keep executor jobs (`hass.async_add_executor_job`) free of any `hass.*async_*` calls.

Reference: https://developers.home-assistant.io/docs/asyncio_thread_safety/

### Do not

- Add synchronous I/O (e.g., `requests`) in HA runtime code.
- Store secrets in the repository.
- Add heavy dependencies unless absolutely necessary.

## Debug logging

When documenting troubleshooting, the logger namespace should match the integration package:

- ✅ `custom_components.bloomin8_eink_canvas: debug`

(If you see other namespaces in older docs, prefer the one above.)

## Debug scripts (lokal)

- `scripts/debug_upload_lenient.py`
  - Debug-Tool, um einen Bild-Upload gegen `/upload` zu testen, selbst wenn das Gerät ungültige HTTP-Response-Header liefert (z. B. doppeltes `Content-Length`).
  - Nutzt nur Python-Stdlib (kein Home Assistant Runtime-Kontext nötig).

## When adding a new feature

- Add/update:
  - constants in `const.py`
  - config/option flow in `config_flow.py` (and migrations if required)
  - entity platform wiring (e.g., `sensor.py`, `button.py`, …)
  - `services.yaml` (if new services are introduced)
  - translations (`strings.json` + `translations/en.json`)
  - **`CHANGELOG.md`** — always document user-facing changes under the `[Unreleased]` section

## PR/commit notes

- Keep changes cohesive (one fix/feature per PR).
- Include a short “why” and “how verified” in the PR description.
- If you changed anything user-facing, add a short note to the README.
