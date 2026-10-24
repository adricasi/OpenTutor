"""iPad pairing: a Cloudflare quick tunnel (HTTPS, so Safari allows the microphone), the LAN URL, QR codes, Bonjour.

The tunnel makes the server reachable from the internet, so requests that arrive through it need the
pairing token: the QR code carries it as `?k=…`, the first request trades it for an HttpOnly cookie and
the app's own fetches and WebSocket work unchanged. LAN and localhost stay open (no token).

`make pair` (`pair.py`) runs the tunnel and writes `.data/pairing/state.json`; the server reads the token
and the tunnel URL from `.data/pairing/` for `GET /api/pairing` and the gate. The cloudflared download and
the tunnel process mirror `get_recordings/src/get_recordings/tunnel.py` (same pinned release, same
repo-local binary in `.tools/bin/`); keep the two in step.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import tarfile
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from http.cookies import SimpleCookie
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode

import segno
from fastapi import FastAPI

from .config import APP_ROOT, Settings

BASE = APP_ROOT.parent  # repo base folder
TOOLS_BIN = BASE / ".tools" / "bin"  # shared with get_recordings: one cached cloudflared

# Pinned release; bump together with get_recordings/tunnel.py.
# (archive sha256 = GitHub asset digest, binary sha256 = release notes)
CLOUDFLARED_VERSION = "2026.10.3"
CLOUDFLARED_SHA256 = {
    "arm64": (
        "1165d14b6f6360a3b8c283d7d1100024d6ec7cd9bc9e32519e96150645f3ec55",
        "f249139e0541fa589c987397d867cc16c83ab1236d31eb6782f2e4552ee9cf80",
    ),
    "x86_64": (
        "81b7b85f2376815c4f59b1aa323887b62e16ce8b67555c2e1577dc23f2a82000",
        "947e2942d6a59922d80190d23b12f3ade8da16e23e3bd937182a48993b7c00db",
    ),
}
URL_RE = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
COOKIE = "inktutor_k"
OPEN_PATHS = {"/healthz"}  # the reachability probe goes through the tunnel without a token
MIC_NOTE = "Plain http: Safari blocks the microphone here (voice needs the HTTPS tunnel). Pen and photos work."


def pairing_dir(data_dir: Path) -> Path:
    return data_dir / "pairing"


# ---- token ----------------------------------------------------------------
def load_token(data_dir: Path, new: bool = False) -> str:
    """Persists across runs so a scanned iPad keeps its cookie; tunnel URLs change anyway."""
    path = pairing_dir(data_dir) / "token"
    if path.exists() and not new:
        return path.read_text().strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(18)
    path.write_text(tok)
    path.chmod(0o600)
    return tok


def read_token(data_dir: Path) -> str | None:
    path = pairing_dir(data_dir) / "token"
    return (path.read_text().strip() or None) if path.exists() else None


# ---- tunnel state (written by `make pair`, read by the server) -----------
def write_state(data_dir: Path, **state: Any) -> None:
    path = pairing_dir(data_dir) / "state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**state, "pid": os.getpid(), "updated": time.time()}))


def clear_state(data_dir: Path) -> None:
    (pairing_dir(data_dir) / "state.json").unlink(missing_ok=True)


def read_state(data_dir: Path) -> dict[str, Any]:
    """The live `make pair` state, or {} if it isn't running (a stale file from a killed run is ignored)."""
    path = pairing_dir(data_dir) / "state.json"
    try:
        state: dict[str, Any] = json.loads(path.read_text())
        os.kill(int(state["pid"]), 0)
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    return state


# ---- LAN ------------------------------------------------------------------
def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 80))  # no packet is sent; picks the outbound interface
            return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"


def local_hostname() -> str:
    """The Bonjour name (`<name>.local`): macOS's LocalHostName, else the short hostname."""
    if platform.system() == "Darwin":
        r = subprocess.run(["scutil", "--get", "LocalHostName"], capture_output=True, text=True, check=False)
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    return socket.gethostname().split(".")[0]


def lan_url(port: int, ip: str | None = None) -> str:
    return f"http://{ip or lan_ip()}:{port}/"


def mdns_url(port: int, name: str | None = None) -> str:
    return f"http://{name or local_hostname()}.local:{port}/"


def with_token(url: str, token: str) -> str:
    """`url` with `k=<token>` added to its query (keeps e.g. `?demo=…`)."""
    base, _, query = url.partition("?")
    q = [*parse_qsl(query, keep_blank_values=True), ("k", token)]
    return f"{base}?{urlencode(q)}"


# ---- QR -------------------------------------------------------------------
def qr_svg_data_uri(url: str) -> str:
    return str(segno.make(url, error="m").svg_data_uri(scale=6, border=2, dark="#1F2328"))


def print_qr(url: str, label: str) -> None:
    print()
    segno.make(url, error="m").terminal(compact=True, border=2)
    print(f"  {label}: {url}", flush=True)


# ---- cloudflared ------------------------------------------------------------
def find_cloudflared() -> Path:
    """Repo-local cloudflared, downloading the pinned release on first use. Never installs system-wide."""
    local = TOOLS_BIN / "cloudflared"
    if local.exists():
        return local
    on_path = shutil.which("cloudflared")
    if on_path:
        return Path(on_path)
    arch = platform.machine()
    if platform.system() != "Darwin" or arch not in CLOUDFLARED_SHA256:
        raise SystemExit(f"No cloudflared for {platform.system()}/{arch}; install it and put it on PATH.")
    asset = f"cloudflared-darwin-{'arm64' if arch == 'arm64' else 'amd64'}.tgz"
    url = f"https://github.com/cloudflare/cloudflared/releases/download/{CLOUDFLARED_VERSION}/{asset}"
    print(f"Downloading cloudflared {CLOUDFLARED_VERSION} into {local.relative_to(BASE)} …", flush=True)
    with tempfile.TemporaryDirectory() as td:
        tgz = Path(td) / asset
        urllib.request.urlretrieve(url, tgz)
        want_tgz, want_bin = CLOUDFLARED_SHA256[arch]
        if (got := hashlib.sha256(tgz.read_bytes()).hexdigest()) != want_tgz:
            raise SystemExit(f"cloudflared archive checksum mismatch ({got}); refusing to run it.")
        with tarfile.open(tgz) as tf:
            member = next(m for m in tf.getmembers() if m.isfile() and m.name.endswith("cloudflared"))
            src = tf.extractfile(member)
            assert src is not None
            data = src.read()
        if (got := hashlib.sha256(data).hexdigest()) != want_bin:
            raise SystemExit(f"cloudflared binary checksum mismatch ({got}); refusing to run it.")
        TOOLS_BIN.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
    local.chmod(0o755)
    return local


class QuickTunnel:
    """One `cloudflared tunnel --url` process. The public URL is random and changes per start.

    `popen` and `binary` are injectable so tests can run it against a fake process.
    """

    def __init__(
        self,
        port: int,
        log_path: Path,
        popen: Callable[..., Any] = subprocess.Popen,
        binary: Path | None = None,
    ) -> None:
        self.port = port
        self.url: str | None = None
        self.proc: Any = None
        self._popen = popen
        self._binary = binary
        self._registered = threading.Event()
        self._log = log_path

    def start(self, timeout: float = 60.0) -> str:
        binary = self._binary or find_cloudflared()
        self._log.parent.mkdir(parents=True, exist_ok=True)
        self.url = None
        self._registered.clear()
        self.proc = self._popen(
            [str(binary), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{self.port}"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,  # Ctrl-C goes to us; we stop it cleanly
        )
        threading.Thread(target=self._pump, daemon=True).start()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.url and self._registered.is_set():
                return self.url
            if self.proc.poll() is not None:
                raise RuntimeError(f"cloudflared exited ({self.proc.returncode}); see {self._log}")
            time.sleep(0.05)
        self.stop()
        raise RuntimeError(f"tunnel did not come up in {timeout:.0f} s; see {self._log}")

    def _pump(self) -> None:
        assert self.proc and self.proc.stderr
        with self._log.open("a", encoding="utf-8") as log:
            for line in self.proc.stderr:
                log.write(line)
                log.flush()
                if not self.url and (m := URL_RE.search(line)):
                    self.url = m.group(0)
                if "Registered tunnel connection" in line:
                    self._registered.set()

    def wait_reachable(self, timeout: float = 90.0) -> bool:
        """GET /healthz through the public URL until it answers.

        The hostname's DNS record appears a few seconds after registration; an early miss is
        negatively cached for at most 60 s (trycloudflare.com SOA minimum), so a short initial
        pause keeps the Mac's resolver from caching it at all.
        """
        assert self.url
        time.sleep(3)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and self.alive():
            r = subprocess.run(
                ["curl", "-s", "-m", "8", "-o", "/dev/null", "-w", "%{http_code}", f"{self.url}/healthz"],
                capture_output=True,
                text=True,
                check=False,
            )
            if r.stdout.strip() == "200":
                return True
            time.sleep(2)
        return False

    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.proc.kill()


# ---- Bonjour ------------------------------------------------------------------
class Bonjour:
    """Advertise `_http._tcp` "Ink Tutor" on the LAN while pairing runs (best effort: never fatal)."""

    def __init__(self, port: int, name: str = "Ink Tutor") -> None:
        self.port, self.name = port, name
        self._zc: Any = None
        self._info: Any = None

    def start(self) -> bool:
        try:
            from zeroconf import ServiceInfo, Zeroconf

            ip = lan_ip()
            self._info = ServiceInfo(
                "_http._tcp.local.",
                f"{self.name}._http._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                properties={"path": "/"},
                server=f"{local_hostname()}.local.",
            )
            self._zc = Zeroconf()
            self._zc.register_service(self._info, allow_name_change=True)
            return True
        except Exception as e:  # no network, port 5353 refused, …: pairing works without it
            print(f"  (Bonjour off: {e})", flush=True)
            self.stop()
            return False

    def stop(self) -> None:
        if self._zc is not None:
            try:
                if self._info is not None:
                    self._zc.unregister_service(self._info)
            finally:
                self._zc.close()
            self._zc = None


# ---- the gate -----------------------------------------------------------------
def is_tunnel_request(headers: dict[str, str]) -> bool:
    """Cloudflare adds cf-connecting-ip / cf-ray to everything it forwards; the Host is the public name."""
    host = headers.get("host", "").split(":")[0].lower()
    return "cf-connecting-ip" in headers or "cf-ray" in headers or host.endswith(".trycloudflare.com")


FORBIDDEN_HTML = (
    b"<!doctype html><meta name=viewport content='width=device-width'><body style='font:17px system-ui;"
    b"padding:24px'><h3>Scan the QR code again</h3><p>This link needs the code shown on the Mac "
    b"(<code>make pair</code>, or <i>Pair iPad</i> on the Ink Tutor start screen).</p>"
)


class TunnelGate:
    """ASGI middleware: requests that came through the tunnel need the pairing token (query once, then cookie)."""

    def __init__(self, app: Any, data_dir: Path) -> None:
        self.app = app
        self.data_dir = data_dir
        self._cache: tuple[int, str | None] = (-1, None)

    def token(self) -> str | None:
        path = pairing_dir(self.data_dir) / "token"
        try:
            mtime = path.stat().st_mtime_ns
        except OSError:
            return None
        if mtime != self._cache[0]:
            self._cache = (mtime, read_token(self.data_dir))
        return self._cache[1]

    async def __call__(self, scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope.get("headers", [])}
        if not is_tunnel_request(headers) or scope["path"] in OPEN_PATHS:
            return await self.app(scope, receive, send)
        token = self.token()
        jar = SimpleCookie(headers.get("cookie", ""))
        got = jar[COOKIE].value if COOKIE in jar else ""
        if token and got and hmac.compare_digest(got, token):
            return await self.app(scope, receive, send)
        query = parse_qsl(scope.get("query_string", b"").decode("latin-1"), keep_blank_values=True)
        k = next((v for name, v in query if name == "k"), "")
        if scope["type"] == "http" and token and k and hmac.compare_digest(k, token):
            rest = urlencode([(n, v) for n, v in query if n != "k"])
            location = scope["path"] + (f"?{rest}" if rest else "")
            cookie = f"{COOKIE}={token}; Path=/; Max-Age=2592000; HttpOnly; SameSite=Lax; Secure"
            return await _respond(
                send, 302, b"", "text/plain", [(b"location", location.encode()), (b"set-cookie", cookie.encode())]
            )
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})  # before accept → HTTP 403
            return None
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


# ---- the API ------------------------------------------------------------------
def pairing_info(settings: Settings) -> dict[str, Any]:
    """What the Pair iPad page shows: the tunnel (if `make pair` runs) and the LAN URLs, with QR codes."""
    state = read_state(settings.data_dir)
    token = read_token(settings.data_dir)
    lan = lan_url(settings.port)
    tunnel = None
    if state.get("url") and token:
        url = with_token(state["url"] + "/", token)
        tunnel = {"url": url, "qr": qr_svg_data_uri(url), "reachable": bool(state.get("reachable"))}
    return {
        "tunnel": tunnel,
        "lan": {"url": lan, "mdns": mdns_url(settings.port), "qr": qr_svg_data_uri(lan), "note": MIC_NOTE},
        "bonjour": "Ink Tutor" if state.get("bonjour") else None,
        "hint": None if tunnel else "Run `make pair` on the Mac for an HTTPS link (needed for the microphone).",
    }


def install(app: FastAPI, settings: Settings) -> None:
    """The tunnel gate + `GET /api/pairing`. Call before the SPA catch-all route is added."""
    app.add_middleware(TunnelGate, data_dir=settings.data_dir)

    @app.get("/api/pairing")
    def get_pairing() -> dict[str, Any]:
        return pairing_info(settings)
