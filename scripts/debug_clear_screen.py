#!/usr/bin/env python3
"""Debug script to test the /clearScreen endpoint directly.

This script helps diagnose issues with the clear_screen API call,
including incomplete HTTP responses that the device sometimes returns.

Usage:
    python scripts/debug_clear_screen.py [IP_ADDRESS]

Default IP: 192.168.188.13
"""

import socket
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime

DEFAULT_IP = "192.168.188.13"
TIMEOUT = 10


def check_device_online(ip: str) -> bool:
    """Quick ping to check if device is reachable."""
    try:
        url = f"http://{ip}/state"
        with urllib.request.urlopen(url, timeout=5) as response:
            return response.status == 200
    except Exception:
        return False


def clear_screen_urllib(ip: str) -> tuple[bool, str]:
    """Try clear_screen using urllib (standard approach)."""
    url = f"http://{ip}/clearScreen"
    try:
        req = urllib.request.Request(url, method="POST")
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            status = response.status
            body = response.read().decode("utf-8", errors="replace")
            return True, f"Status: {status}, Body: {body}"
    except urllib.error.HTTPError as e:
        return False, f"HTTPError: {e.code} {e.reason}"
    except urllib.error.URLError as e:
        return False, f"URLError: {e.reason}"
    except Exception as e:
        return False, f"Exception: {type(e).__name__}: {e}"


def clear_screen_raw_socket(ip: str, port: int = 80) -> tuple[bool, str]:
    """Try clear_screen using raw socket (lenient approach).
    
    This can handle malformed HTTP responses that urllib rejects.
    """
    request = (
        f"POST /clearScreen HTTP/1.1\r\n"
        f"Host: {ip}\r\n"
        f"Connection: close\r\n"
        f"Content-Length: 0\r\n"
        f"\r\n"
    )
    
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(TIMEOUT)
        sock.connect((ip, port))
        sock.sendall(request.encode("utf-8"))
        
        # Read response
        response_parts = []
        while True:
            try:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response_parts.append(chunk)
            except socket.timeout:
                break
        
        sock.close()
        response = b"".join(response_parts).decode("utf-8", errors="replace")
        
        # Parse status line
        lines = response.split("\r\n")
        if lines and lines[0].startswith("HTTP/"):
            status_line = lines[0]
            # Extract headers
            headers = {}
            body_start = 0
            for i, line in enumerate(lines[1:], 1):
                if line == "":
                    body_start = i + 1
                    break
                if ":" in line:
                    key, val = line.split(":", 1)
                    headers[key.strip().lower()] = val.strip()
            
            body = "\r\n".join(lines[body_start:]) if body_start < len(lines) else ""
            
            return True, f"{status_line}\nHeaders: {headers}\nBody: {body}"
        else:
            return False, f"Invalid response: {response[:200]}"
            
    except socket.timeout:
        return False, "Socket timeout (no response received)"
    except ConnectionRefusedError:
        return False, "Connection refused"
    except Exception as e:
        return False, f"Exception: {type(e).__name__}: {e}"


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_IP
    
    print(f"🧹 Testing /clearScreen on BLOOMIN8 E-Ink Canvas at {ip}")
    print(f"   Timestamp: {datetime.now().isoformat()}")
    print("=" * 60)
    
    # 1. Check if device is online
    print("\n📡 Checking device connectivity...")
    if check_device_online(ip):
        print("   ✅ Device is online")
    else:
        print("   ❌ Device appears offline or unreachable")
        print("   (The device may be in deep sleep)")
        return
    
    # 2. Test with urllib (standard)
    print("\n🔧 Test 1: urllib (standard HTTP client)")
    print("-" * 40)
    start = time.time()
    success, result = clear_screen_urllib(ip)
    elapsed = time.time() - start
    
    if success:
        print(f"   ✅ Success ({elapsed:.2f}s)")
        print(f"   {result}")
    else:
        print(f"   ❌ Failed ({elapsed:.2f}s)")
        print(f"   {result}")
    
    # 3. Test with raw socket (lenient)
    print("\n🔧 Test 2: Raw socket (lenient HTTP)")
    print("-" * 40)
    start = time.time()
    success, result = clear_screen_raw_socket(ip)
    elapsed = time.time() - start
    
    if success:
        print(f"   ✅ Success ({elapsed:.2f}s)")
        for line in result.split("\n"):
            print(f"   {line}")
    else:
        print(f"   ❌ Failed ({elapsed:.2f}s)")
        print(f"   {result}")
    
    # 4. Verify device still online after clear
    print("\n📡 Verifying device still online after clear...")
    time.sleep(1)
    if check_device_online(ip):
        print("   ✅ Device is still online - command was likely processed")
    else:
        print("   ⚠️  Device is now offline (may have entered sleep)")
    
    print("\n" + "=" * 60)
    print("Done!")


if __name__ == "__main__":
    main()
