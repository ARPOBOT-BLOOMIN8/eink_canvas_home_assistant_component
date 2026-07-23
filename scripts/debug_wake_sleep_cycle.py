#!/usr/bin/env python3
"""Debug script: exercise HTTP + BLE wake/sleep/reboot paths.

Goal
----
Reproduce the same primitives the Home Assistant integration uses:
- HTTP: /state, /deviceInfo, /sleep, /reboot
- BLE wake: write payload 0x01 to known wake characteristics

This script is intentionally self-contained and friendly to run from a dev box
or on the Home Assistant host.

Examples
--------
# Check HTTP endpoints only
python3 scripts/debug_wake_sleep_cycle.py --host 192.168.188.13 --http-only

# BLE wake by address (Linux typically uses MAC)
python3 scripts/debug_wake_sleep_cycle.py --host 192.168.188.13 --ble-wake --ble-target F4:90:42:16:3F:47

# BLE wake by scanning for a name substring (macOS / CoreBluetooth uses UUID-like addresses)
python3 scripts/debug_wake_sleep_cycle.py --host 192.168.188.13 --ble-wake --ble-name "Bloomin8"

# Full cycle: sleep -> BLE wake -> check /deviceInfo -> optional reboot
python3 scripts/debug_wake_sleep_cycle.py --host 192.168.188.13 --cycle --ble-target F4:90:42:16:3F:47
"""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import cast


def _load_const_module() -> object:
    """Load `custom_components/bloomin8_eink_canvas/const.py` without importing HA runtime.

    Importing `custom_components.bloomin8_eink_canvas.*` would execute
    `__init__.py`, which depends on Home Assistant and other third-party libs.
    For a debug script we only need constants, so we load the file directly.
    """

    repo_root = Path(__file__).resolve().parents[1]
    const_path = repo_root / "custom_components" / "bloomin8_eink_canvas" / "const.py"
    if not const_path.exists():
        raise FileNotFoundError(f"const.py not found at {const_path}")

    spec = importlib.util.spec_from_file_location("bloomin8_eink_canvas_const", const_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to create import spec for const.py")

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


try:
    _const = _load_const_module()
    BLE_WAKE_CHAR_UUIDS = getattr(_const, "BLE_WAKE_CHAR_UUIDS")
    # Prefer the newer "pulse" sequence (0x01 then 0x00) when available.
    _ble_wake_pulse = getattr(_const, "BLE_WAKE_PULSE", None)
    BLE_WAKE_PULSE_GAP_SECONDS = float(getattr(_const, "BLE_WAKE_PULSE_GAP_SECONDS", 0.05))
    if _ble_wake_pulse is None:
        ble_wake_payload = getattr(_const, "BLE_WAKE_PAYLOAD")
        BLE_WAKE_PULSE: tuple[bytes, bytes] = (ble_wake_payload, b"\x00")
    else:
        BLE_WAKE_PULSE = cast(tuple[bytes, bytes], _ble_wake_pulse)
    ENDPOINT_DEVICE_INFO = getattr(_const, "ENDPOINT_DEVICE_INFO")
    ENDPOINT_REBOOT = getattr(_const, "ENDPOINT_REBOOT")
    ENDPOINT_SLEEP = getattr(_const, "ENDPOINT_SLEEP")
    ENDPOINT_STATUS = getattr(_const, "ENDPOINT_STATUS")
except Exception as err:
    print(f"❌ Failed to load integration constants: {type(err).__name__}: {err}")
    print("   Tip: run from the repository checkout (so scripts/ and custom_components/ are present).")
    sys.exit(2)


@dataclass
class StepResult:
    name: str
    ok: bool
    dt: float
    detail: str = ""


def _http_url(host: str, path: str) -> str:
    if not path.startswith("/"):
        path = "/" + path
    return f"http://{host}{path}"


def http_get_json(host: str, path: str, *, timeout: int = 5) -> tuple[dict | None, StepResult]:
    url = _http_url(host, path)
    t0 = time.monotonic()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            dt = time.monotonic() - t0
            try:
                return json.loads(raw), StepResult(f"GET {path}", True, dt, f"status={getattr(resp, 'status', '?')}")
            except json.JSONDecodeError:
                # best-effort extraction
                start = raw.find("{")
                end = raw.rfind("}") + 1
                if start >= 0 and end > start:
                    try:
                        return json.loads(raw[start:end]), StepResult(
                            f"GET {path}", True, dt, f"status={getattr(resp, 'status', '?')} (extracted JSON)"
                        )
                    except json.JSONDecodeError:
                        return None, StepResult(f"GET {path}", False, dt, "invalid JSON")
                return None, StepResult(f"GET {path}", False, dt, "invalid JSON")
    except urllib.error.HTTPError as e:
        dt = time.monotonic() - t0
        # Any HTTP response still proves the device is reachable.
        return None, StepResult(f"GET {path}", False, dt, f"HTTP {e.code}")
    except Exception as e:
        dt = time.monotonic() - t0
        return None, StepResult(f"GET {path}", False, dt, f"{type(e).__name__}: {e}")


def http_post(host: str, path: str, *, timeout: int = 5) -> StepResult:
    url = _http_url(host, path)
    t0 = time.monotonic()
    req = urllib.request.Request(url, data=b"", method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            _ = resp.read()  # drain
            return StepResult(f"POST {path}", True, time.monotonic() - t0, f"status={getattr(resp, 'status', '?')}")
    except urllib.error.HTTPError as e:
        return StepResult(f"POST {path}", False, time.monotonic() - t0, f"HTTP {e.code}")
    except Exception as e:
        return StepResult(f"POST {path}", False, time.monotonic() - t0, f"{type(e).__name__}: {e}")


async def ble_wake(*, target: str | None, name_substring: str | None, scan_timeout: float, connect_timeout: float) -> StepResult:
    """Send the BLE wake payload to the device.

    On Linux this is typically the MAC address.
    On macOS (CoreBluetooth), addresses are UUID-like; scanning by name is more reliable.
    """

    try:
        from bleak import BleakClient, BleakScanner  # type: ignore
    except Exception as err:
        return StepResult("BLE wake", False, 0.0, f"bleak not available: {type(err).__name__}: {err}")

    addr = (target or "").strip() or None

    if addr is None:
        # Scan and find by name substring
        if not name_substring:
            return StepResult("BLE wake", False, 0.0, "need --ble-target or --ble-name")

        t_scan0 = time.monotonic()
        devices = await BleakScanner.discover(timeout=scan_timeout)
        # Prefer exact name matches, then substring.
        chosen = None
        for d in devices:
            if d.name and d.name == name_substring:
                chosen = d
                break
        if chosen is None:
            for d in devices:
                if d.name and name_substring.lower() in d.name.lower():
                    chosen = d
                    break
        if chosen is None:
            dt = time.monotonic() - t_scan0
            return StepResult("BLE scan", False, dt, f"no device found matching name '{name_substring}'")

        addr = chosen.address

    t0 = time.monotonic()
    try:
        # BleakClient can take address string; we wrap connect in a timeout.
        async with BleakClient(addr) as client:
            await asyncio.wait_for(client.connect(), timeout=connect_timeout)
            last_err: Exception | None = None
            for char_uuid in BLE_WAKE_CHAR_UUIDS:
                try:
                    try:
                        await asyncio.wait_for(
                            client.write_gatt_char(char_uuid, BLE_WAKE_PULSE[0], response=True),
                            timeout=2,
                        )
                    except asyncio.TimeoutError:
                        await client.write_gatt_char(char_uuid, BLE_WAKE_PULSE[0], response=False)

                    # Release pulse (best-effort)
                    try:
                        if BLE_WAKE_PULSE_GAP_SECONDS and BLE_WAKE_PULSE_GAP_SECONDS > 0:
                            await asyncio.sleep(float(BLE_WAKE_PULSE_GAP_SECONDS))
                        await client.write_gatt_char(char_uuid, BLE_WAKE_PULSE[1], response=False)
                    except Exception:
                        # Non-fatal for the debug script; the main wake write succeeded.
                        pass

                    last_err = None
                    break
                except Exception as err:  # best-effort fallbacks
                    last_err = err
            if last_err is not None:
                raise last_err

        return StepResult("BLE wake", True, time.monotonic() - t0, f"target={addr}")
    except Exception as err:
        return StepResult("BLE wake", False, time.monotonic() - t0, f"{type(err).__name__}: {err}")


def print_step(r: StepResult) -> None:
    status = "✅" if r.ok else "❌"
    print(f"{status} {r.name:14s}  dt={r.dt:5.2f}s  {r.detail}")


async def main_async(args: argparse.Namespace) -> int:
    print(f"Host: {args.host}")

    # 1) Baseline HTTP checks
    data, r = http_get_json(args.host, ENDPOINT_STATUS, timeout=args.http_timeout)
    print_step(r)

    data, r = http_get_json(args.host, ENDPOINT_DEVICE_INFO, timeout=args.http_timeout)
    print_step(r)
    if data:
        print(f"   device: name={data.get('name')} version={data.get('version')} ip={data.get('sta_ip')} max_idle={data.get('max_idle')} sleep_duration={data.get('sleep_duration')}")

    if args.http_only:
        return 0

    # 2) Optional cycle
    if args.sleep:
        r = http_post(args.host, ENDPOINT_SLEEP, timeout=args.http_timeout)
        print_step(r)
        # IMPORTANT: Any HTTP request may keep the device awake / reset idle timers.
        # To verify that /sleep actually put the device offline, we optionally wait
        # WITHOUT doing any HTTP probes and then check reachability with short timeouts.
        if args.verify_sleep:
            print(
                f"   (verify-sleep) Waiting {args.verify_sleep_wait:.1f}s without HTTP requests to let the device go offline…"
            )
            await asyncio.sleep(args.verify_sleep_wait)

            _, r = http_get_json(args.host, ENDPOINT_STATUS, timeout=args.verify_sleep_http_timeout)
            print_step(r)
            _, r = http_get_json(args.host, ENDPOINT_DEVICE_INFO, timeout=args.verify_sleep_http_timeout)
            print_step(r)
        else:
            await asyncio.sleep(args.after_sleep_wait)

    if args.ble_wake:
        r = await ble_wake(
            target=args.ble_target,
            name_substring=args.ble_name,
            scan_timeout=args.ble_scan_timeout,
            connect_timeout=args.ble_connect_timeout,
        )
        print_step(r)
        await asyncio.sleep(args.after_wake_wait)

    # 3) Check deviceInfo after wake
    data, r = http_get_json(args.host, ENDPOINT_DEVICE_INFO, timeout=args.http_timeout)
    print_step(r)
    if data:
        print(f"   device: name={data.get('name')} version={data.get('version')} ip={data.get('sta_ip')} max_idle={data.get('max_idle')} sleep_duration={data.get('sleep_duration')}")

    # 4) Optional reboot (useful to clear weird stuck states)
    if args.reboot:
        r = http_post(args.host, ENDPOINT_REBOOT, timeout=args.http_timeout)
        print_step(r)

    return 0


def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="BLOOMIN8 debug: wake/sleep/reboot cycle")
    p.add_argument("--host", default="192.168.188.13", help="Device IP/host")

    p.add_argument("--http-timeout", type=int, default=5, help="HTTP timeout seconds")
    p.add_argument("--http-only", action="store_true", help="Only query HTTP endpoints")

    p.add_argument("--sleep", action="store_true", help="POST /sleep")
    p.add_argument("--reboot", action="store_true", help="POST /reboot")

    p.add_argument("--ble-wake", action="store_true", help="Send BLE wake payload")
    p.add_argument("--ble-target", default=None, help="BLE address/MAC/UUID")
    p.add_argument("--ble-name", default=None, help="Scan and match by name substring")
    p.add_argument("--ble-scan-timeout", type=float, default=6.0, help="BLE scan timeout seconds")
    p.add_argument("--ble-connect-timeout", type=float, default=20.0, help="BLE connect timeout seconds")

    p.add_argument("--after-sleep-wait", type=float, default=2.0, help="Seconds to wait after sleep")
    p.add_argument("--after-wake-wait", type=float, default=2.0, help="Seconds to wait after wake")

    p.add_argument(
        "--verify-sleep",
        action="store_true",
        help=(
            "After POST /sleep, wait without sending HTTP requests and then probe /state and /deviceInfo "
            "with short timeouts to see if the device is actually offline/asleep."
        ),
    )
    p.add_argument(
        "--verify-sleep-wait",
        type=float,
        default=20.0,
        help="Seconds to wait (without HTTP) before probing reachability after /sleep",
    )
    p.add_argument(
        "--verify-sleep-http-timeout",
        type=int,
        default=2,
        help="HTTP timeout seconds for the reachability probes during verify-sleep",
    )

    p.add_argument(
        "--cycle",
        action="store_true",
        help="Convenience: run sleep + ble wake + deviceInfo check (equivalent to --sleep --ble-wake)",
    )
    return p


def main() -> None:
    p = build_arg_parser()
    args = p.parse_args()

    if args.cycle:
        args.sleep = True
        args.ble_wake = True

    rc = asyncio.run(main_async(args))
    raise SystemExit(rc)


if __name__ == "__main__":
    main()
