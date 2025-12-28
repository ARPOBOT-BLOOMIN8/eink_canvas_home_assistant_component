# BLOOMIN8 E-Ink Canvas for Home Assistant

[](https://github.com/custom-components/hacs) [](https://github.com/4seacz/bloomin8_eink_canvas_home_assistant/releases) [](https://www.google.com/search?q=LICENSE)


## Getting Started

First things first, you gotta get the canvas on your network.

### 1\. Use the BLOOMIN8 Mobile App

[](https://apps.apple.com/us/app/bloomin8/id6737453755?platform=iphone) [](https://play.google.com/store/apps/details?id=com.play.mobile.bloomin8)

Use it to:

  * Wake the device up with Bluetooth (super useful when it's being sleepy).
  * Connect it to your WiFi network.
  * **Grab the IP address.** You'll need this.

### Optional: Bluetooth wake in Home Assistant (recommended for Deep Sleep)

If your Canvas often sleeps (Deep Sleep) or is not reachable via IP right away, you can optionally set up Bluetooth wake inside Home Assistant:

  * **BLE Wake Button (optional):** If you configure the device's Bluetooth MAC address, Home Assistant will expose a **Wake (Bluetooth)** button entity.
  * **Auto-wake before HTTP actions (optional):** Enable `ble_auto_wake` to let the integration try a best-effort BLE wake **before sending HTTP API commands**.

Notes:

  * If you set the device's **Max Idle Time** very low (e.g. **10 seconds**), the Canvas may fall asleep between Home Assistant actions and UI interactions. In this case even with BLE wake actions could take long.

Requirements:

  * Home Assistant Bluetooth (local adapter) **or** ESPHome Bluetooth proxies
  * A connectable BLE device in Home Assistant's Bluetooth cache

Tip: The Mobile App Bluetooth wake is still useful as a quick fallback if you just need to wake it once.

### 2\. Install the Integration

<!-- The easy way (HACS), which you should totally do:

1.  Open HACS in Home Assistant.
2.  Search for "BLOOMIN8 E-Ink Canvas" and install it.
3.  Restart Home Assistant (we know, it's annoying, but it's the law).

The manual way (if you like living on the edge): -->

1.  Download the latest release from GitHub.
2.  Unzip and dump the `bloomin8_eink_canvas` folder into your `custom_components` directory.
3.  Restart. Told you.

### 3\. Add it to Home Assistant

1.  Go to `Settings` \> `Devices & Services`.
2.  Click `Add Integration` and search for `BLOOMIN8`.
3.  Pop in the IP address you noted down earlier.
4.  Give it a name. Something fun, like `Living Room Portal` or `The Void`.

-----

## What You Can Do With It

### 🛠️ Available Services

All integration services are available under the domain `bloomin8_eink_canvas`:

#### System Control

  * `bloomin8_eink_canvas.show_next`: Display the next image in the current gallery or playlist.
  * `bloomin8_eink_canvas.sleep`: Put the device into sleep mode.
  * `bloomin8_eink_canvas.reboot`: Restart the device.
  * `bloomin8_eink_canvas.clear_screen`: Clear the display screen.
  * `bloomin8_eink_canvas.whistle`: Send a wake/keep-alive signal.
  * `bloomin8_eink_canvas.refresh_device_info`: Manually refresh and update device information.
  * `bloomin8_eink_canvas.update_settings`: Update device settings (sleep duration, idle time, wake sensitivity, name).

#### Image (Upload / Delete)

  * `bloomin8_eink_canvas.upload_image_url`: Download an image from a URL, convert to JPEG, optionally process for screen, upload, optionally show immediately.
  * `bloomin8_eink_canvas.upload_image_data`: Upload base64-encoded image bytes, convert to JPEG, optionally process for screen.
  * `bloomin8_eink_canvas.upload_images_multi`: Batch upload multiple images in one request.
  * `bloomin8_eink_canvas.upload_dithered_image_data`: Upload pre-processed dithered raw image data (advanced).
  * `bloomin8_eink_canvas.delete_image`: Delete a specific image from a gallery.

#### Gallery

  * `bloomin8_eink_canvas.create_gallery`: Create a new empty gallery.
  * `bloomin8_eink_canvas.delete_gallery`: Delete a gallery and all contained images.
  * `bloomin8_eink_canvas.list_galleries`: List galleries on the device (may return a service response depending on Home Assistant version).

#### Playlist

  * `bloomin8_eink_canvas.show_playlist`: Start playlist playback on the device.
  * `bloomin8_eink_canvas.put_playlist`: Create or overwrite a playlist definition.
  * `bloomin8_eink_canvas.delete_playlist`: Delete a playlist.
  * `bloomin8_eink_canvas.list_playlists`: List playlists (may return a service response depending on Home Assistant version).
  * `bloomin8_eink_canvas.get_playlist`: Get one playlist definition (may return a service response depending on Home Assistant version).

#### Home Assistant standard services (optional)

These are Home Assistant core services (not `bloomin8_eink_canvas.*`), but they work well with this integration:

  * `media_player.play_media`: Send an image to the Canvas via the media player entity.
  * `button.press`: Press button entities exposed by the integration (e.g., Sleep, Refresh Info, Wake Bluetooth).

**Media Browser:** Browse your device's galleries or upload new images directly from the Home Assistant media browser. It's slick.


**Display a new family photo every morning:**

```yaml
service: media_player.play_media
target:
  entity_id: media_player.living_room_portal
data:
  media_content_type: "image/jpeg"
  media_content_id: "/media/local/photos/family_photo_of_the_day.jpg"
```

**Put the frame to sleep when you go to bed:**

```yaml
service: button.press
target:
  entity_id: button.living_room_portal_sleep
```

Here are some ideas our team cooked up:

  * **Morning Routine:** Show an inspiring, AI-generated landscape at 7 AM.
  * **Weather Display:** Show a sunny image when it's nice out, or a rainy one when it's gloomy.
  * **Evening Wind-down:** Switch to calm, minimalist art when your "Goodnight" scene runs.
  * **Smart Sleep:** Automatically adjust sleep duration based on season or schedule.
  * **Storage Monitoring:** Get notified when storage is running low.
  * **Auto-refresh:** If you enable polling, Home Assistant can periodically refresh device info to keep status current.

-----

## Power saving / Polling

**Polling can keep a device awake — this integration therefore implements polling in a deliberately "safe" way.**

Important: **"Max Idle Time" / `max_idle` is a device setting** (Canvas firmware), not just an internal Home Assistant option.
You can change it in Home Assistant via:

  * the **"Max Idle Time" select entity** (device configuration), or
  * the **`bloomin8_eink_canvas.update_settings`** service (field `max_idle`).

By default, `enable_polling` is **disabled** so the Canvas can sleep (Deep Sleep) and save power/battery.

Practical tip: If you configure **Max Idle Time** to a very low value (e.g. 10–30s), expect more frequent wake-ups. This can make automations feel flaky unless BLE wake is configured (Wake button / `ble_auto_wake`).

If you want state updates without preventing sleep:

  * Use the **Refresh Info** button or call `bloomin8_eink_canvas.refresh_device_info` (recommended for low power).
  * Or enable `enable_polling` in the integration configuration: the integration will poll using an interval **larger than the device's Max Idle Time** (derived from `max_idle`) so it should **not** keep the Canvas awake.

-----

## Troubleshooting (When Things Go Wrong)

**Canvas not responding?**

  * Is the IP address correct? Did it change?
  * Are HA and the canvas on the same network? No VLAN weirdness?
  * **Pro tip:** Try waking it up with the mobile app's Bluetooth function first. This solves 90% of issues.

**Image upload failed?**

  * Is the file path in Home Assistant correct?
  * It's an e-ink display, so high-contrast images look best.

**Status not updating?**
  * Make sure your device is awake.
  * Try pressing the **Refresh Info** button or calling the `bloomin8_eink_canvas.refresh_device_info` service.
  * Check the **Device Info** sensor for connection status.
  * Restart the integration (or HA itself).

**Still stuck? Enable debug logs.** Add this to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.bloomin8_eink_canvas: debug
```



-----

**Find Us:** [Official Website](https://bloomin8.com) | [API Docs](https://bloomin8.readme.io) | [Business Contact](mailto:hello@bloomin8.com)



© 2025 BLOOMIN8. All rights reserved.
