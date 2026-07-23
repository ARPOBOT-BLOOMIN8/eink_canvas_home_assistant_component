#!/usr/bin/env python3
"""Debug script: upload an image to Bloomin8 E‑Ink Canvas and tolerate broken HTTP responses.

Why this exists:
Some firmware versions send invalid HTTP response headers (e.g., duplicate
Content-Length). Strict HTTP clients (like aiohttp) fail while parsing the response.
This script uploads via a raw socket and parses the response leniently.

Usage examples:
  python3 scripts/debug_upload_lenient.py --host 192.168.188.13 \
    --url 'https://images.unsplash.com/photo-1531259683007-016a7b628fc3?q=80&w=1587&auto=format&fit=crop&ixlib=rb-4.1.0&ixid=M3wxMjA3fDB8MHxwaG90by1wYWdlfHx8fGVufDB8fHx8fA%3D%3D' \
    --filename unsplash_test.jpg --gallery default --show-now

Notes:
- This script uses only the Python standard library.
- It does not resize or convert the image; it uploads the downloaded bytes.
  (The HA integration does conversion/resizing before uploading.)
"""

from __future__ import annotations

import argparse
import json
import socket
import time
import urllib.parse
import urllib.request


def _snap_to_supported_sizes(width: int, height: int) -> tuple[int, int]:
    """Snap to Bloomin8 documented fixed sizes.

    - 7.3 inch: 480x800
    - 13.3 inch: 1200x1600
    """
    w = int(width or 0)
    h = int(height or 0)
    if w <= 0 or h <= 0:
        return 1200, 1600
    if max(w, h) <= 900:
        return 480, 800
    return 1200, 1600


def maybe_process_to_jpeg(
    raw: bytes,
    *,
    target_width: int,
    target_height: int,
    horizontal_display: bool,
    fit_mode: str,
) -> tuple[bytes, str]:
    """Best-effort processing to device-ready JPEG.

    Uses Pillow if available; otherwise returns the original bytes.

    horizontal_display:
      Bloomin8 docs: if you want a horizontal image (1600x1200), rotate it 90° clockwise.
      For 13.3" this means: fit to 1600x1200, then rotate to 1200x1600 before upload.
    """
    try:
        from io import BytesIO

        from PIL import Image  # type: ignore
    except Exception:
        return raw, "application/octet-stream"

    def cover(img: "Image.Image", tw: int, th: int) -> "Image.Image":
        ia = img.width / img.height
        ta = tw / th
        if ia > ta:
            sh = th
            sw = int(th * ia)
        else:
            sw = tw
            sh = int(tw / ia)
        scaled = img.resize((sw, sh), Image.Resampling.LANCZOS)
        xo = (sw - tw) // 2
        yo = (sh - th) // 2
        return scaled.crop((xo, yo, xo + tw, yo + th))

    def contain(img: "Image.Image", tw: int, th: int) -> "Image.Image":
        ia = img.width / img.height
        ta = tw / th
        if ia > ta:
            sw = tw
            sh = int(tw / ia)
        else:
            sh = th
            sw = int(th * ia)
        scaled = img.resize((sw, sh), Image.Resampling.LANCZOS)
        bg = Image.new("RGB", (tw, th), (255, 255, 255))
        xo = (tw - sw) // 2
        yo = (th - sh) // 2
        bg.paste(scaled, (xo, yo))
        return bg

    img = Image.open(BytesIO(raw))
    # Flatten alpha
    if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
        bg = Image.new("RGB", img.size, (255, 255, 255))
        if img.mode == "P":
            img = img.convert("RGBA")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    elif img.mode != "RGB":
        img = img.convert("RGB")

    if horizontal_display:
        # Fit to horizontal canvas first (e.g. 1600x1200), then rotate 90° clockwise.
        horizontal_w, horizontal_h = target_height, target_width
        if fit_mode == "contain":
            img = contain(img, horizontal_w, horizontal_h)
        else:
            img = cover(img, horizontal_w, horizontal_h)
        img = img.rotate(-90, expand=True)
    else:
        if fit_mode == "contain":
            img = contain(img, target_width, target_height)
        else:
            img = cover(img, target_width, target_height)

    out = BytesIO()
    img.save(out, format="JPEG", quality=95)
    return out.getvalue(), "image/jpeg"


def _split_host_port(host: str) -> tuple[str, int]:
    host = host.strip()
    if host.count(":") == 1 and not host.startswith("["):
        h, p = host.split(":", 1)
        try:
            return h.strip(), int(p)
        except ValueError:
            return host, 80
    return host, 80


def download(url: str, timeout: int = 30) -> tuple[bytes, str | None, str | None]:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "ha-bloomin8-debug/1.0",
            "Accept": "image/*,*/*;q=0.8",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # nosec - debug tool
        content_type = resp.headers.get("Content-Type")
        final_url = resp.geturl()
        data = resp.read()
        return data, content_type, final_url


def build_multipart(*, field_name: str, filename: str, content_type: str, data: bytes, boundary: str) -> bytes:
    safe_filename = (filename or "image.jpg").replace('"', "")
    prefix = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"{field_name}\"; filename=\"{safe_filename}\"\r\n"
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return prefix + (data or b"") + suffix


def _recv_until(sock: socket.socket, marker: bytes, limit: int = 64 * 1024) -> tuple[bytes, bytes]:
    buf = bytearray()
    while marker not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf += chunk
        if len(buf) > limit:
            raise ConnectionError("HTTP header too large")
    if marker not in buf:
        return bytes(buf), b""
    head, rest = bytes(buf).split(marker, 1)
    return head, rest


def _read_exact(sock: socket.socket, n: int) -> bytes:
    out = bytearray()
    while len(out) < n:
        chunk = sock.recv(n - len(out))
        if not chunk:
            raise ConnectionError("Unexpected EOF")
        out += chunk
    return bytes(out)


def _readline(sock: socket.socket) -> bytes:
    out = bytearray()
    while True:
        ch = sock.recv(1)
        if not ch:
            break
        out += ch
        if out.endswith(b"\n"):
            break
    return bytes(out)


def read_response_lenient(sock: socket.socket) -> tuple[int, dict[str, str], bytes]:
    header_bytes, rest = _recv_until(sock, b"\r\n\r\n")
    if not header_bytes:
        raise ConnectionError("Empty response")

    lines = header_bytes.split(b"\r\n")
    status_line = lines[0].decode("iso-8859-1", errors="replace")
    try:
        status = int(status_line.split(" ", 2)[1])
    except Exception as err:
        raise ConnectionError(f"Bad status line: {status_line!r} ({err})") from err

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line or b":" not in line:
            continue
        k, v = line.split(b":", 1)
        key = k.decode("iso-8859-1", errors="replace").strip().lower()
        val = v.decode("iso-8859-1", errors="replace").strip()
        # keep first occurrence to avoid duplicate Content-Length issues
        headers.setdefault(key, val)

    body = bytearray(rest)

    te = headers.get("transfer-encoding", "").lower()
    if "chunked" in te:
        # chunked decoding
        while True:
            size_line = _readline(sock)
            if not size_line:
                break
            size_str = size_line.strip().split(b";", 1)[0]
            chunk_size = int(size_str, 16)
            if chunk_size == 0:
                # consume trailing CRLF and ignore trailers
                _readline(sock)
                break
            body += _read_exact(sock, chunk_size)
            _read_exact(sock, 2)  # CRLF
        return status, headers, bytes(body)

    cl = headers.get("content-length")
    if cl is not None:
        try:
            total = int(cl)
        except ValueError:
            total = None
        if total is not None:
            missing = total - len(body)
            if missing > 0:
                body += _read_exact(sock, missing)
            return status, headers, bytes(body[:total])

    # no length known: read until close
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        body += chunk

    return status, headers, bytes(body)


def upload_lenient(*, host: str, path: str, body: bytes, content_type: str, timeout: int = 30) -> tuple[int, dict[str, str], bytes]:
    h, port = _split_host_port(host)

    request_lines = [
        f"POST {path} HTTP/1.1",
        f"Host: {h}",
        "User-Agent: ha-bloomin8-debug/1.0",
        "Accept: */*",
        "Connection: close",
        f"Content-Type: {content_type}",
        f"Content-Length: {len(body)}",
    ]
    request = ("\r\n".join(request_lines) + "\r\n\r\n").encode("utf-8") + body

    sock = socket.create_connection((h, port), timeout=5)
    sock.settimeout(timeout)
    try:
        sock.sendall(request)
        return read_response_lenient(sock)
    finally:
        try:
            sock.close()
        except Exception:
            pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", required=True, help="Device host, e.g. 192.168.188.13 or 192.168.188.13:80")
    p.add_argument("--url", required=True, help="Image URL to download")
    p.add_argument("--filename", default=None, help="Filename to store on device")
    p.add_argument("--gallery", default="default")
    p.add_argument("--show-now", action="store_true", help="Pass show_now=1")
    p.add_argument(
        "--panel",
        choices=["7.3", "13.3"],
        default="13.3",
        help='Target panel size. 7.3" => 480x800, 13.3" => 1200x1600 (default).',
    )
    p.add_argument(
        "--horizontal",
        action="store_true",
        help="Prepare a horizontal image (e.g. 1600x1200) and rotate 90° clockwise as per Bloomin8 docs",
    )
    p.add_argument(
        "--fit",
        choices=["cover", "contain"],
        default="cover",
        help="How to fit the source into the target size when Pillow is available (default: cover)",
    )
    p.add_argument(
        "--no-process",
        action="store_true",
        help="Disable image processing even if Pillow is available",
    )
    p.add_argument("--timeout", type=int, default=30)
    args = p.parse_args()

    raw, content_type, final_url = download(args.url, timeout=args.timeout)

    filename = args.filename
    if not filename:
        # derive a best-effort name from final URL path
        try:
            parsed = urllib.parse.urlparse(final_url or args.url)
            base = (parsed.path.rsplit("/", 1)[-1] or "downloaded")
            if not base.lower().endswith((".jpg", ".jpeg")):
                base += ".jpg"
            filename = base
        except Exception:
            filename = f"debug_{int(time.time())}.jpg"

    # The device endpoint expects JPEG; try to process/convert when possible.
    if args.panel == "7.3":
        target_w, target_h = 480, 800
    else:
        target_w, target_h = 1200, 1600
    target_w, target_h = _snap_to_supported_sizes(target_w, target_h)

    if args.no_process:
        payload = raw
        upload_ct = "image/jpeg" if (content_type or "").lower().startswith("image/") else "application/octet-stream"
    else:
        payload, upload_ct = maybe_process_to_jpeg(
            raw,
            target_width=target_w,
            target_height=target_h,
            horizontal_display=bool(args.horizontal),
            fit_mode=str(args.fit),
        )

    boundary = f"----ha-bloomin8-debug-{int(time.time() * 1000)}"
    body = build_multipart(
        field_name="image",
        filename=filename,
        content_type=upload_ct,
        data=payload,
        boundary=boundary,
    )

    query = urllib.parse.urlencode(
        {
            "filename": filename,
            "gallery": args.gallery,
            "show_now": 1 if args.show_now else 0,
        }
    )
    path = f"/upload?{query}"
    ct = f"multipart/form-data; boundary={boundary}"

    print(f"Downloaded {len(raw)} bytes (Content-Type: {content_type}, final_url: {final_url})")
    if args.no_process:
        print("Processing: disabled")
    else:
        print(
            f"Processing: panel={args.panel}, target={target_w}x{target_h}, fit={args.fit}, horizontal={bool(args.horizontal)} (Pillow optional)"
        )
    print(f"Upload payload: {len(payload)} bytes, content-type: {upload_ct}")
    print(f"Uploading to http://{args.host}{path}")

    status, headers, resp_body = upload_lenient(
        host=args.host,
        path=path,
        body=body,
        content_type=ct,
        timeout=args.timeout,
    )

    print(f"Response status: {status}")
    print("Response headers (first occurrence kept):")
    for k in sorted(headers):
        print(f"  {k}: {headers[k]}")

    preview = resp_body[:500]
    try:
        preview_text = preview.decode("utf-8", errors="replace")
    except Exception:
        preview_text = repr(preview)

    print("Response body preview (first 500 bytes):")
    print(preview_text)

    # Try parse JSON body
    try:
        text = resp_body.decode("utf-8", errors="replace")
        parsed = json.loads(text)
        print("Parsed JSON response:")
        print(json.dumps(parsed, indent=2, ensure_ascii=False))
    except Exception as err:
        print(f"JSON parse failed: {err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
