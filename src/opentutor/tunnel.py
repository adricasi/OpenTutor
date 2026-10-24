"""Optional online demo link: a token-gated Cloudflare quick tunnel (REQ-017, ADR-0006).

Adapted from tutor-ipad/tutor-app/apps/server/src/inktutor_server/pairing.py (QuickTunnel,
TunnelGate, with_token). Differences: Linux binary, a dedicated loopback listener so every
tunnel request is remote, and a per-start in-memory token with a session-lifetime cookie.
"""

from __future__ import annotations

import hashlib
import hmac
import platform
import re
import secrets
import shutil
import subprocess
import threading
import time
import urllib.request
from collections.abc import Callable, Mapping
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

CLOUDFLARED_VERSION = "2026.10.3"
# SHA-256 of the GitHub release asset, recorded on first download (trust on first use):
# cross-check against the checksums in Cloudflare's release notes when bumping the version.
CLOUDFLARED_SHA256 = {
    "x86_64": "053b80b0a23031a038fdbf3c073f59f82b8f1f152aa67971d7383ffafc0dbab0",
}
TOOLS_BIN = Path(".tools/bin")
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
COOKIE = "ot_demo"
BLOCKED_PATHS = ("/teacher", "/healthz")  # never through the tunnel (AC-017.4)

FORBIDDEN_HTML = (
    b"<!doctype html><meta name=viewport content='width=device-width'>"
    b"<body style='font:17px system-ui;padding:24px'><h1>Scan the QR code again</h1>"
    b"<p>This online demo link needs the code shown on the hub laptop.</p>"
)


def with_token(url: str, token: str) -> str:
    base, _, query = url.partition("?")
    return f"{base}?{urlencode([*parse_qsl(query, keep_blank_values=True), ('k', token)])}"


def find_cloudflared() -> Path | None:
    for candidate in (TOOLS_BIN / "cloudflared.exe", TOOLS_BIN / "cloudflared"):
        if candidate.exists():
            return candidate
    on_path = shutil.which("cloudflared")
    return Path(on_path) if on_path else None


def download_cloudflared() -> Path:
    """Fetch the pinned Linux release into .tools/bin and verify its SHA-256."""
    arch = platform.machine()
    if platform.system() != "Linux" or arch not in CLOUDFLARED_SHA256:
        raise RuntimeError(f"No pinned cloudflared for {platform.system()}/{arch}; install it.")
    url = (
        "https://github.com/cloudflare/cloudflared/releases/download/"
        f"{CLOUDFLARED_VERSION}/cloudflared-linux-amd64"
    )
    with urllib.request.urlopen(url, timeout=120) as response:  # noqa: S310 - fixed https URL
        data = response.read()
    if hashlib.sha256(data).hexdigest() != CLOUDFLARED_SHA256[arch]:
        raise RuntimeError("cloudflared checksum mismatch; refusing to install it.")
    TOOLS_BIN.mkdir(parents=True, exist_ok=True)
    target = TOOLS_BIN / "cloudflared"
    target.write_bytes(data)
    target.chmod(0o755)
    return target


class QuickTunnel:
    """One `cloudflared tunnel --url` process; the public URL is random and changes per start."""

    def __init__(
        self, port: int, binary: Path, popen: Callable[..., Any] = subprocess.Popen
    ) -> None:
        self.port, self.binary, self._popen = port, binary, popen
        self.url: str | None = None
        self.proc: Any = None
        self._registered = threading.Event()

    def start(self, timeout: float = 60.0) -> str:
        self.url = None
        self._registered.clear()
        self.proc = self._popen(
            [
                str(self.binary),
                "tunnel",
                "--no-autoupdate",
                "--url",
                f"http://127.0.0.1:{self.port}",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        threading.Thread(target=self._pump, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url and self._registered.is_set():
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError(f"cloudflared exited ({self.proc.returncode})")
            time.sleep(0.05)
        self.stop()
        raise RuntimeError(f"the tunnel did not come up in {timeout:.0f} s")

    def _pump(self) -> None:
        for line in self.proc.stderr:
            if not self.url and (match := URL_RE.search(line)):
                self.url = match.group(0)
            if "Registered tunnel connection" in line:
                self._registered.set()

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.alive():
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


def is_tunnel(scope: Mapping[str, Any], tunnel_port: int) -> bool:
    server = scope.get("server")
    return server is not None and server[1] == tunnel_port


class TunnelGate:
    """ASGI middleware for the tunnel listener: token in the QR link, then a session cookie."""

    def __init__(self, app: Any, tunnel_port: int, token: str) -> None:
        self.app, self.tunnel_port, self.token = app, tunnel_port, token

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] != "http" or not is_tunnel(scope, self.tunnel_port):
            await self.app(scope, receive, send)
            return None
        if scope["path"].startswith(BLOCKED_PATHS):
            return await _respond(send, 404, b"Not found", "text/plain")
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        jar = SimpleCookie(headers.get("cookie", ""))
        if COOKIE in jar and hmac.compare_digest(jar[COOKIE].value, self.token):
            await self.app(scope, receive, send)
            return None
        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        given = next((value for name, value in query if name == "k"), "")
        if given and hmac.compare_digest(given, self.token):
            rest = urlencode([(name, value) for name, value in query if name != "k"])
            location = scope["path"] + (f"?{rest}" if rest else "")
            cookie = f"{COOKIE}={self.token}; Path=/; HttpOnly; SameSite=Lax; Secure"
            return await _respond(
                send,
                302,
                b"",
                "text/plain",
                [(b"location", location.encode()), (b"set-cookie", cookie.encode())],
            )
        return await _respond(send, 403, FORBIDDEN_HTML, "text/html; charset=utf-8")


async def _respond(
    send: Any, status: int, body: bytes, ctype: str, extra: list[tuple[bytes, bytes]] | None = None
) -> None:
    headers = [
        (b"content-type", ctype.encode()),
        (b"content-length", str(len(body)).encode()),
        (b"cache-control", b"no-store"),
        (b"referrer-policy", b"no-referrer"),
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def new_token() -> str:
    return secrets.token_urlsafe(18)
