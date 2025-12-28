#!/usr/bin/env python3
"""Debug script to query /deviceInfo and /settings endpoints directly.

Usage:
    python scripts/debug_device_info.py [IP_ADDRESS]

Default IP: 192.168.188.13
"""

import json
import sys
import urllib.request
import urllib.error
from datetime import datetime

DEFAULT_IP = "192.168.188.13"


def fetch_json(url: str, timeout: int = 10) -> dict | None:
    """Fetch JSON from URL, handling malformed responses."""
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            text = response.read().decode("utf-8", errors="replace")
            # Try direct parse
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                # Try to extract JSON from malformed response
                start = text.find("{")
                end = text.rfind("}") + 1
                if start >= 0 and end > start:
                    return json.loads(text[start:end])
                print(f"  ⚠️  Could not parse JSON: {text[:200]}")
                return None
    except urllib.error.URLError as e:
        print(f"  ❌ Connection failed: {e}")
        return None
    except Exception as e:
        print(f"  ❌ Error: {e}")
        return None


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP
    print(f"🔍 Debugging BLOOMIN8 E-Ink Canvas at {ip}")
    print(f"   Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)

    # 1. Check /state (quick connectivity check)
    print("\n📡 Checking /state ...")
    state_url = f"http://{ip}/state"
    state = fetch_json(state_url)
    if state:
        print(f"   ✅ Device is online")
        print(f"   Raw response: {json.dumps(state, indent=2)}")
    else:
        print(f"   ❌ Device appears offline or unreachable")
        print("   (The device may be in deep sleep)")
        return

    # 2. Get /deviceInfo
    print("\n📋 Fetching /deviceInfo ...")
    device_info_url = f"http://{ip}/deviceInfo"
    device_info = fetch_json(device_info_url)
    if device_info:
        print(f"   ✅ Got device info")
        
        # Highlight key power/sleep fields
        print("\n   🔋 Power & Sleep Settings:")
        print(f"      max_idle      = {device_info.get('max_idle', 'N/A')}")
        print(f"      sleep_duration = {device_info.get('sleep_duration', 'N/A')}")
        print(f"      battery       = {device_info.get('battery', 'N/A')}%")
        
        print("\n   📺 Display Info:")
        print(f"      name          = {device_info.get('name', 'N/A')}")
        print(f"      version       = {device_info.get('version', 'N/A')}")
        print(f"      board_model   = {device_info.get('board_model', 'N/A')}")
        print(f"      screen_model  = {device_info.get('screen_model', 'N/A')}")
        print(f"      resolution    = {device_info.get('width', '?')}x{device_info.get('height', '?')}")
        
        print("\n   🌐 Network:")
        print(f"      network_type  = {device_info.get('network_type', 'N/A')}")
        print(f"      sta_ssid      = {device_info.get('sta_ssid', 'N/A')}")
        print(f"      sta_ip        = {device_info.get('sta_ip', 'N/A')}")
        
        print("\n   📁 Storage:")
        total = device_info.get('total_size', 0)
        free = device_info.get('free_size', 0)
        used = total - free
        if total > 0:
            pct = round(used / total * 100, 1)
            print(f"      storage       = {pct}% used ({used // 1024 // 1024} MB / {total // 1024 // 1024} MB)")
        
        print("\n   🎨 Current State:")
        print(f"      gallery       = {device_info.get('gallery', 'N/A')}")
        print(f"      playlist      = {device_info.get('playlist', 'N/A')}")
        print(f"      play_type     = {device_info.get('play_type', 'N/A')}")
        print(f"      current_image = {device_info.get('current_image', 'N/A')}")
        
        print("\n   📄 Full /deviceInfo response:")
        print(json.dumps(device_info, indent=2, ensure_ascii=False))
    else:
        print("   ❌ Failed to get device info")

    # 3. Try /settings endpoint (may have more detail)
    print("\n⚙️  Fetching /settings ...")
    settings_url = f"http://{ip}/settings"
    settings = fetch_json(settings_url)
    if settings:
        print(f"   ✅ Got settings")
        print("\n   📄 Full /settings response:")
        print(json.dumps(settings, indent=2, ensure_ascii=False))
        
        # Compare max_idle from both endpoints
        if device_info:
            di_max_idle = device_info.get("max_idle")
            s_max_idle = settings.get("max_idle")
            print(f"\n   🔍 max_idle comparison:")
            print(f"      /deviceInfo: {di_max_idle}")
            print(f"      /settings:   {s_max_idle}")
            if di_max_idle != s_max_idle:
                print(f"      ⚠️  VALUES DIFFER!")
    else:
        print("   ⚠️  /settings endpoint not available or returned error")

    # 4. Summary
    print("\n" + "=" * 60)
    print("📊 ANALYSIS")
    print("=" * 60)
    if device_info:
        max_idle = device_info.get("max_idle")
        if max_idle == 300:
            print(f"   ⚠️  max_idle = 300 (5 minutes) - This is the DEFAULT value.")
            print("      Possible reasons:")
            print("      1. Device firmware defaults to 300s")
            print("      2. Setting was never changed via /settings endpoint")
            print("      3. Device resets settings on reboot/wake")
            print("")
            print("   💡 To change max_idle, try:")
            print(f"      curl -X POST 'http://{ip}/settings?max_idle=600'")
            print("      (This sets it to 10 minutes)")
        elif max_idle == -1:
            print(f"   ✅ max_idle = -1 (Never sleep) - Device will stay awake")
        elif max_idle and max_idle > 0:
            print(f"   ✅ max_idle = {max_idle}s ({max_idle // 60} min {max_idle % 60}s)")
        else:
            print(f"   ❓ max_idle = {max_idle} (unexpected value)")


if __name__ == "__main__":
    main()
