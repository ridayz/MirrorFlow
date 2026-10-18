"""Sesi mirror untuk satu device.

Alur (persis seperti scrcpy):
  1. push scrcpy-server.jar ke /data/local/tmp
  2. adb forward tcp:<port> localabstract:scrcpy_<scid>
  3. jalankan app_process di device
  4. sambung socket VIDEO -> baca dummy byte (1) lalu tunggu
  5. sambung socket CONTROL
  6. baca 64 byte nama device, sisanya diteruskan mentah ke browser

Browser yang mem-parsing framing (codec id, session packet, media packet),
jadi sisi Python hanya jadi pipa + kanal kontrol.
"""

from __future__ import annotations

import random
import socket
import threading
import time
from collections import deque

from . import config, control
from .adb import Adb, AdbError
from .hub import SubscriberHub

DEVICE_NAME_LEN = 64
DUMMY_LEN = 1


class SessionError(RuntimeError):
    pass


class DeviceSession(SubscriberHub):
    """Satu device yang sedang di-mirror."""

    def __init__(self, adb: Adb, serial: str, options: dict[str, object] | None = None):
        super().__init__()
        self.adb = adb
        self.serial = serial
        self.options = dict(config.DEFAULT_OPTIONS)
        if options:
            self.options.update({k: v for k, v in options.items() if k in self.options})

        self.port = 0
        self.scid = 0
        self.device_name = ""
        self.running = False
        self.error: str | None = None
        self.started_at = 0.0

        self.bytes_sent = 0
        self.clients = 0

        self._video: socket.socket | None = None
        self._control: socket.socket | None = None
        self._proc = None
        self._threads: list[threading.Thread] = []
        self._log: deque[str] = deque(maxlen=120)
        self._stop = threading.Event()

    # -- status ------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "serial": self.serial,
            "name": self.device_name or self.serial,
            "running": self.running,
            "error": self.error,
            "port": self.port,
            "bytes": self.bytes_sent,
            "clients": self.clients,
            "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "options": self.options,
            "screen": f"{self._sniff.width}x{self._sniff.height}" if self._sniff.width else None,
            "log": list(self._log)[-25:],
        }

    # -- kontrol -----------------------------------------------------------
    def send_control(self, data: bytes) -> bool:
        sock = self._control
        if not sock:
            return False
        try:
            sock.sendall(data)
            return True
        except OSError as exc:
            self._note(f"kontrol gagal: {exc}")
            return False

    # -- pub/sub -----------------------------------------------------------
    def request_keyframe(self) -> None:
        """Force device mengirim IDR baru supaya klien baru bisa langsung decode."""
        if self.running:
            self.send_control(control.reset_video())

    def _note(self, message: str) -> None:
        self._log.append(f"{time.strftime('%H:%M:%S')} {message}")

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        if not config.SCRCPY_SERVER_JAR.is_file():
            raise SessionError(f"scrcpy-server tidak ada: {config.SCRCPY_SERVER_JAR}")

        self.error = None
        self._stop.clear()
        self._note(f"push {config.SCRCPY_SERVER_JAR.name}")
        self.adb.push(self.serial, str(config.SCRCPY_SERVER_JAR), config.SERVER_JAR_REMOTE)

        self.port = config.free_port()
        self.scid = random.getrandbits(31)
        socket_name = f"scrcpy_{self.scid:08x}"
        self._note(f"forward tcp:{self.port} -> localabstract:{socket_name}")
        self.adb.forward(self.serial, f"tcp:{self.port}", f"localabstract:{socket_name}")

        opts: dict[str, object] = {
            "scid": f"{self.scid:08x}",
            "log_level": "info",
            "audio": False,
            "control": True,
            "tunnel_forward": True,
            # jangan kirim pesan device->client di kanal kontrol (belum kita pakai)
            "clipboard_autosync": False,
            "cleanup": True,
            "power_off_on_close": False,
            "stay_awake": bool(self.options.get("stay_awake", True)),
            "show_touches": bool(self.options.get("show_touches", False)),
        }
        if int(self.options.get("max_size") or 0) > 0:
            opts["max_size"] = int(self.options["max_size"])
        if int(self.options.get("max_fps") or 0) > 0:
            opts["max_fps"] = int(self.options["max_fps"])
        if int(self.options.get("video_bit_rate") or 0) > 0:
            opts["video_bit_rate"] = int(self.options["video_bit_rate"])

        self._note("jalankan scrcpy-server di device")
        self._proc = self.adb.spawn_server(self.serial, config.SCRCPY_VERSION, opts)

        try:
            self._video = self._open_video(timeout=10.0)
            # dummy byte sudah dibaca di _open_video; server menunggu socket kontrol
            self._control = self._open_control(timeout=6.0)
            name = _recv_exact(self._video, DEVICE_NAME_LEN)
            self.device_name = name.decode("utf-8", "replace").rstrip("\0").strip()
            self._note(f"terhubung: {self.device_name or self.serial}")
        except Exception as exc:
            self._teardown()
            self.error = str(exc)
            raise SessionError(str(exc)) from exc

        self.running = True
        self.started_at = time.time()
        self._start_threads()

    def _open_video(self, timeout: float) -> socket.socket:
        """Sambung socket video; dummy byte dipakai untuk memastikan server siap."""
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                sock = socket.create_connection((config.HOST, self.port), timeout=2.0)
            except OSError as exc:
                last = exc
                time.sleep(0.1)
                continue
            try:
                sock.settimeout(2.0)
                dummy = _recv_exact(sock, DUMMY_LEN)
                if not dummy:
                    raise SessionError("server menutup koneksi (belum siap)")
                sock.settimeout(None)
                return sock
            except (OSError, SessionError) as exc:
                last = exc
                sock.close()
                time.sleep(0.1)
        raise SessionError(f"tidak bisa membuka socket video: {last}")

    def _open_control(self, timeout: float) -> socket.socket:
        deadline = time.monotonic() + timeout
        last: Exception | None = None
        while time.monotonic() < deadline and not self._stop.is_set():
            try:
                sock = socket.create_connection((config.HOST, self.port), timeout=2.0)
                sock.settimeout(None)
                return sock
            except OSError as exc:
                last = exc
                time.sleep(0.05)
        raise SessionError(f"tidak bisa membuka socket kontrol: {last}")

    def _start_threads(self) -> None:
        reader = threading.Thread(target=self._read_video, name=f"video-{self.serial}", daemon=True)
        reader.start()
        self._threads.append(reader)

        log_thread = threading.Thread(target=self._drain_server_log, name=f"log-{self.serial}", daemon=True)
        log_thread.start()
        self._threads.append(log_thread)

    def _read_video(self) -> None:
        sock = self._video
        assert sock is not None
        try:
            while not self._stop.is_set():
                chunk = sock.recv(1 << 16)
                if not chunk:
                    break
                self._publish(chunk)
        except OSError as exc:
            if not self._stop.is_set():
                self._note(f"video terputus: {exc}")
        finally:
            if self.running and not self._stop.is_set():
                self.error = "stream berhenti (device terlepas atau layar mati?)"
                self._note(self.error)
            self.running = False

    def _drain_server_log(self) -> None:
        proc = self._proc
        if not proc or not proc.stdout:
            return
        try:
            for raw in proc.stdout:
                line = raw.decode("utf-8", "replace").rstrip()
                if line:
                    self._note(line)
        except (OSError, ValueError):
            pass

    def stop(self) -> None:
        self._teardown()

    def _teardown(self) -> None:
        self._stop.set()
        self.running = False
        self.reset_stream()

        for sock in (self._video, self._control):
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
                try:
                    sock.close()
                except OSError:
                    pass
        self._video = None
        self._control = None

        proc = self._proc
        self._proc = None
        if proc and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except Exception:
                proc.kill()

        if self.port:
            self.adb.forward_remove(self.serial, f"tcp:{self.port}")

        self.close_subscribers()

        self._note("sesi dihentikan")

    # -- helper kontrol tingkat tinggi ------------------------------------
    def inject(self, name: str, **kwargs) -> bool:
        """Jalankan perintah kontrol berdasarkan nama (dipakai dari UI)."""
        try:
            data = _build_command(name, kwargs)
        except KeyError:
            return False
        if data is None:
            return False
        return self.send_control(data)


def _build_command(name: str, kw: dict) -> bytes | None:
    if name == "touch":
        pointer = str(kw.get("pointer", "mouse"))
        pointer_id = {
            "finger": control.POINTER_GENERIC_FINGER,
            "vfinger": control.POINTER_VIRTUAL_FINGER,
        }.get(pointer, control.POINTER_MOUSE)
        return control.touch(
            int(kw["action"]),
            int(kw["x"]),
            int(kw["y"]),
            int(kw["w"]),
            int(kw["h"]),
            pressure=float(kw.get("pressure", 1.0)),
            action_button=int(kw.get("action_button", 0)),
            buttons=int(kw.get("buttons", 0)),
            pointer_id=pointer_id,
        )
    if name == "key":
        return control.keycode(
            int(kw.get("action", control.ACTION_DOWN)),
            int(kw["code"]),
            int(kw.get("repeat", 0)),
            int(kw.get("meta", 0)),
        )
    if name == "text":
        return control.text(str(kw["text"]))
    if name == "scroll":
        return control.scroll(
            int(kw["x"]), int(kw["y"]), int(kw["w"]), int(kw["h"]),
            float(kw.get("hscroll", 0)), float(kw.get("vscroll", 0)),
            int(kw.get("buttons", 0)),
        )
    if name == "back":
        return control.back_or_screen_on(int(kw.get("action", control.ACTION_DOWN)))
    if name == "power":
        return control.set_display_power(bool(kw.get("on", True)))
    if name == "rotate":
        return control.rotate_device()
    if name == "notifications":
        return control.expand_notification_panel()
    if name == "settings":
        return control.expand_settings_panel()
    if name == "collapse":
        return control.collapse_panels()
    if name == "clipboard_set":
        return control.set_clipboard(int(kw.get("sequence", 0)), str(kw.get("text", "")), bool(kw.get("paste", False)))
    if name == "clipboard_get":
        return control.get_clipboard(int(kw.get("copy_key", 1)))
    if name == "reset_video":
        return control.reset_video()
    if name == "scan_file":
        return control.scan_file(str(kw.get("path", "")))
    return None


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    """Baca tepat `count` byte; b'' kalau koneksi ditutup lebih awal."""
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return bytes(buf)
        buf += chunk
    return bytes(buf)


__all__ = ["DeviceSession", "SessionError", "Adb", "AdbError"]
