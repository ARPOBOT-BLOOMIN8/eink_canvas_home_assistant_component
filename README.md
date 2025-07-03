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


#### System Control

  * `eink_display.show_next`: Flips to the next image in the gallery.
  * `eink_display.sleep`: Puts the device to sleep. Sweet dreams.
  * `eink_display.reboot`: The classic "turn it off and on again."
  * `eink_display.clear_screen`: Wipes the screen to a clean slate.
  * `eink_display.whistle`: Wakes the device up or keeps it from falling asleep.
  * `eink_display.update_settings`: Change device settings like sleep duration on the fly.
  * `eink_display.refresh_device_info`: Forces a poll for the latest device status.

#### Image & Gallery Management

  * `media_player.play_media`: The main service for sending a new image to the display.
  * **Media Browser:** Browse your device's galleries or upload new images directly from the Home Assistant media browser. It's slick.


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
  * **Auto-refresh:** Periodically refresh device info to keep status current.

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
  * Try pressing the **Refresh Info** button or calling the `eink_display.refresh_device_info` service.
  * Check the **Device Info** sensor for connection status.
  * Restart the integration (or HA itself).

**Still stuck? Enable debug logs.** Add this to your `configuration.yaml`:

```yaml
logger:
  default: warning
  logs:
    custom_components.eink_display: debug
```



-----

**Find Us:** [Official Website](https://bloomin8.com) | [API Docs](https://bloomin8.readme.io) | [Business Contact](mailto:hello@bloomin8.com)



© 2025 BLOOMIN8. All rights reserved.
