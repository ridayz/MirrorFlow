"""HTTP + WebSocket server lokal.

Endpoint:
  GET  /                      -> UI
  GET  /static/<berkas>       -> aset UI
  GET  /api/state             -> adb, daftar device, daftar sesi
  GET  /api/device/<serial>   -> detail satu device
  POST /api/session/start     -> {serial, options?}
  POST /api/session/stop      -> {serial}
  GET  /ws                    -> WebSocket: video (binary) + kontrol (JSON)
"""

from __future__ import annotations

import json
import os
import queue
import sys
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from . import __version__, config, ws
from .adb import Adb, AdbError
from .mock import MockSession
from .session import DeviceSession, SessionError

DEMO_SERIAL = "demo"

# set MIRRORFLOW_DEBUG=1 untuk melihat jejak koneksi (berguna saat bikin device nyambung)
DEBUG = os.environ.get("MIRRORFLOW_DEBUG") == "1"


def _dbg(*args: object) -> None:
    if DEBUG:
        print("[mf]", *args, file=sys.stderr, flush=True)


_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
}


class Manager:
    """Pemegang semua sesi mirror."""

    def __init__(self, demo: bool = False):
        self.adb: Adb | None = None
        self.adb_error: str | None = None
        try:
            self.adb = Adb()
        except AdbError as exc:
            self.adb_error = str(exc)

        self.sessions: dict[str, DeviceSession] = {}
        self.demo: MockSession | None = MockSession() if demo else None
        self._lock = threading.Lock()

    # -- info --------------------------------------------------------------
    def devices(self) -> list[dict]:
        if not self.adb:
            return []
        try:
            return [dev.to_json() for dev in self.adb.devices(with_props=True)]
        except AdbError:
            return []

    def device_detail(self, serial: str) -> dict:
        if self.demo and serial == DEMO_SERIAL:
            size = f"{self.demo.width}x{self.demo.height}"
            return {
                "serial": DEMO_SERIAL,
                "name": self.demo.device_name,
                "model": "Demo Device",
                "brand": "MirrorFlow",
                "android": "demo",
                "sdk": "-",
                "abi": "-",
                "screen": size,
                "online": True,
            }
        if not self.adb:
            raise AdbError(self.adb_error or "adb tidak tersedia")

        target = next((d for d in self.adb.devices() if d.serial == serial), None)
        if target is None:
            raise AdbError(f"device tidak ditemukan: {serial}")
        if not target.online:
            raise AdbError(f"device {serial} berstatus {target.state}")

        props = self.adb.props(serial)
        width, height = self.adb.screen_size(serial)
        return {
            "serial": serial,
            "name": props.get("ro.product.model", serial),
            "model": props.get("ro.product.model", ""),
            "brand": props.get("ro.product.brand", ""),
            "android": props.get("ro.build.version.release", ""),
            "sdk": props.get("ro.build.version.sdk", ""),
            "abi": props.get("ro.product.cpu.abi", ""),
            "screen": f"{width}x{height}" if width else "-",
            "online": True,
        }

    def state(self) -> dict:
        with self._lock:
            sessions = [s.to_json() for s in self.sessions.values()]
        if self.demo:
            sessions.insert(0, self.demo.to_json())
        return {
            "version": __version__,
            "adb": self.adb.path if self.adb else None,
            "adbError": self.adb_error,
            "demo": self.demo is not None,
            "devices": self.devices(),
            "sessions": sessions,
        }

    # -- sesi --------------------------------------------------------------
    def get(self, serial: str):
        if self.demo and serial == DEMO_SERIAL:
            return self.demo
        return self.sessions.get(serial)

    def start(self, serial: str, options: dict | None = None):
        if self.demo and serial == DEMO_SERIAL:
            self.demo.start()
            return self.demo
        if not self.adb:
            raise SessionError(self.adb_error or "adb tidak tersedia")

        with self._lock:
            existing = self.sessions.get(serial)
            if existing and existing.running:
                return existing
            session = DeviceSession(self.adb, serial, options)
            session.start()
            self.sessions[serial] = session
            return session

    def stop(self, serial: str) -> None:
        if self.demo and serial == DEMO_SERIAL:
            self.demo.stop()
            return
        with self._lock:
            session = self.sessions.pop(serial, None)
        if session:
            session.stop()


class Handler(BaseHTTPRequestHandler):
    server_version = f"MirrorFlow/{__version__}"
    protocol_version = "HTTP/1.1"
    manager: Manager

    # -- util --------------------------------------------------------------
    def log_message(self, fmt: str, *args) -> None:  # senyapkan access log
        pass

    def _send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _serve_file(self, relative: str) -> None:
        target = (config.STATIC / relative).resolve()
        if not str(target).startswith(str(config.STATIC.resolve())) or not target.is_file():
            self._send_json({"error": "berkas tidak ditemukan", "path": relative}, 404)
            return
        body = target.read_bytes()
        ctype = _CONTENT_TYPES.get(target.suffix.lower(), "application/octet-stream")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {}

    # -- routing -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/ws":
            self._handle_ws()
            return
        if path in ("/", "/index.html"):
            self._serve_file("index.html")
            return
        if path.startswith("/static/"):
            self._serve_file(unquote(path[len("/static/"):]))
            return
        if path == "/api/state":
            self._send_json(self.manager.state())
            return
        if path.startswith("/api/device/"):
            serial = unquote(path[len("/api/device/"):])
            try:
                self._send_json(self.manager.device_detail(serial))
            except AdbError as exc:
                self._send_json({"error": str(exc)}, 400)
            return
        self._send_json({"error": "rute tidak dikenal", "path": path}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        payload = self._read_json()

        if path == "/api/session/start":
            serial = str(payload.get("serial", ""))
            if not serial:
                self._send_json({"error": "serial wajib"}, 400)
                return
            try:
                session = self.manager.start(serial, payload.get("options"))
                self._send_json({"ok": True, "session": session.to_json()})
            except (SessionError, AdbError) as exc:
                self._send_json({"error": str(exc)}, 500)
            return

        if path == "/api/session/stop":
            serial = str(payload.get("serial", ""))
            self.manager.stop(serial)
            self._send_json({"ok": True})
            return

        self._send_json({"error": "rute tidak dikenal", "path": path}, 404)

    # -- websocket ---------------------------------------------------------
    def _handle_ws(self) -> None:
        key = self.headers.get("Sec-WebSocket-Key")
        if not key:
            self._send_json({"error": "bukan permintaan websocket"}, 400)
            return

        self.send_response(101)
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", ws.accept_key(key))
        self.end_headers()
        self.close_connection = True

        conn = ws.WSConnection(self.rfile, self.wfile)
        session_state: dict = {"serial": None, "token": None, "stop": None, "thread": None}
        manager = self.manager
        tag = f"c{self.client_address[1]}"
        _dbg(tag, "ws dibuka")

        def send_json(obj: dict) -> None:
            conn.send_text(json.dumps(obj, ensure_ascii=False))

        def detach() -> None:
            session = manager.get(session_state["serial"]) if session_state["serial"] else None
            if session and session_state["token"] is not None:
                session.unsubscribe(session_state["token"])
                _dbg(tag, "detach", session_state["serial"], "token", session_state["token"])
            if session_state["stop"]:
                session_state["stop"].set()
            session_state.update({"serial": None, "token": None, "stop": None, "thread": None})

        def attach(serial: str) -> None:
            detach()
            session = manager.get(serial)
            if session is None:
                send_json({"t": "error", "message": f"sesi belum jalan: {serial}"})
                return
            token, chan = session.subscribe()
            stop = threading.Event()
            sent = {"chunks": 0, "bytes": 0}

            def pump() -> None:
                _dbg(tag, "pump mulai", serial)
                while not stop.is_set():
                    try:
                        chunk = chan.get(timeout=0.5)
                    except queue.Empty:
                        continue
                    if chunk is None:
                        break
                    if not conn.send_binary(chunk):
                        _dbg(tag, "pump: kirim gagal")
                        break
                    sent["chunks"] += 1
                    sent["bytes"] += len(chunk)
                _dbg(tag, "pump berhenti", sent)
                send_json({"t": "detached", "serial": serial})

            # PENTING: kirim `attached` SEBELUM pompa video jalan, supaya klien
            # sempat me-reset parser sebelum byte pertama tiba.
            session_state.update({"serial": serial, "token": token, "stop": stop, "thread": None})
            send_json({"t": "attached", "serial": serial, "session": session.to_json()})
            _dbg(tag, "attach", serial, "token", token)

            thread = threading.Thread(target=pump, name=f"pump-{serial}", daemon=True)
            session_state["thread"] = thread
            thread.start()

        send_json({"t": "hello", "version": __version__, **manager.state()})

        try:
            while True:
                message = conn.recv()
                if message is None:
                    break
                opcode, payload = message
                if opcode != ws.OP_TEXT:
                    continue
                try:
                    data = json.loads(payload.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    continue
                try:
                    self._dispatch(data, send_json, attach, detach)
                except Exception as exc:  # jangan biarkan satu galat mematikan sesi
                    _dbg(tag, "galat dispatch:", type(exc).__name__, exc)
                    send_json({"t": "error", "message": f"{type(exc).__name__}: {exc}"})
        except OSError as exc:
            _dbg(tag, "ws OSError:", exc)
        finally:
            _dbg(tag, "ws ditutup")
            detach()
            conn.close()

    def _dispatch(self, data: dict, send_json, attach, detach) -> None:
        kind = data.get("t")
        manager = self.manager

        if kind == "attach":
            _dbg("pesan: attach")
            attach(str(data.get("serial", "")))
            return

        if kind == "detach":
            detach()
            return

        if kind == "refresh":
            send_json({"t": "state", **manager.state()})
            return

        if kind == "start":
            serial = str(data.get("serial", ""))
            try:
                session = manager.start(serial, data.get("options"))
                send_json({"t": "started", "session": session.to_json()})
                self._broadcast_state()
            except (SessionError, AdbError) as exc:
                send_json({"t": "error", "message": str(exc)})
            return

        if kind == "stop":
            manager.stop(str(data.get("serial", "")))
            send_json({"t": "stopped"})
            self._broadcast_state()
            return

        if kind == "command":
            session = manager.get(str(data.get("serial", "")))
            if session is None:
                send_json({"t": "error", "message": "sesi tidak aktif"})
                return
            name = str(data.get("name", ""))
            args = data.get("args") or {}
            if isinstance(args, dict):
                session.inject(name, **args)
            return

    def _broadcast_state(self) -> None:
        # state hanya dikirim ke klien yang memintanya; cukup diamkan di sini
        return


def serve(host: str = config.HOST, port: int = config.HTTP_PORT, demo: bool = False,
          open_browser: bool = True) -> None:
    manager = Manager(demo=demo)
    handler = _make_handler(manager)

    httpd = None
    last_error: Exception | None = None
    for candidate in range(port, port + 20):
        try:
            httpd = ThreadingHTTPServer((host, candidate), handler)
            port = candidate
            break
        except OSError as exc:
            last_error = exc
    if httpd is None:
        raise RuntimeError(f"tidak bisa bind port {host}:{port} ({last_error})")

    httpd.daemon_threads = True
    url = f"http://{host}:{port}/"

    print(f"MirrorFlow {__version__}")
    print(f"  UI      : {url}")
    print(f"  adb     : {manager.adb.path if manager.adb else '(tidak ada) ' + str(manager.adb_error)}")
    print(f"  mode    : {'DEMO (tanpa HP)' if demo else 'device asli'}")
    print("  berhenti: Ctrl+C")

    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.shutdown()
        for serial in list(manager.sessions):
            manager.stop(serial)
        if demo:
            manager.stop(DEMO_SERIAL)


def _make_handler(manager: Manager):
    """Bikin subclass Handler dengan manager terpasang sebagai atribut kelas."""

    class _BoundHandler(Handler):
        pass

    _BoundHandler.manager = manager  # type: ignore[attr-defined]
    return _BoundHandler
