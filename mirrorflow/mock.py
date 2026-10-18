"""Mode demo: memutar berkas H.264 lokal dengan framing persis seperti scrcpy-server.

Tujuannya supaya seluruh jalur (framing -> WebSocket -> WebCodecs -> canvas)
bisa dites tanpa HP Android sama sekali. Dari sisi browser tidak ada bedanya
dengan device asli.
"""

from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from . import config
from .hub import SubscriberHub

CODEC_ID_H264 = 0x68323634  # b"h264"
PACKET_FLAG_SESSION = 1 << 63
PACKET_FLAG_CONFIG = 1 << 62
PACKET_FLAG_KEY_FRAME = 1 << 61

START_CODE = b"\x00\x00\x01"
DEMO_H264 = config.ASSETS / "demo.h264"
DEMO_META = config.ASSETS / "demo.json"


def split_annex_b(data: bytes) -> list[bytes]:
    """Pecah stream Annex-B jadi daftar NAL unit (tanpa start code)."""
    units: list[bytes] = []
    positions: list[tuple[int, int]] = []
    i = 0
    n = len(data)
    while i < n - 2:
        if data[i] == 0 and data[i + 1] == 0 and data[i + 2] == 1:
            if i > 0 and data[i - 1] == 0:
                # start code 4 byte ikut terpakai
                positions.append((i - 1, i + 3))
            else:
                positions.append((i, i + 3))
            i += 3
        else:
            i += 1
    for index, (_, start) in enumerate(positions):
        end = positions[index + 1][0] if index + 1 < len(positions) else n
        if end > start:
            units.append(data[start:end])
    return units


# NAL non-VCL: SEI, SPS, PPS, AUD
NON_VCL = (6, 7, 8, 9)
PARAM_NALS = (7, 8)


def _starts_new_picture(nal: bytes) -> bool:
    """True kalau `first_mb_in_slice == 0`, yaitu slice pertama sebuah picture.

    first_mb_in_slice adalah ue(v) pertama di slice header. Nilainya 0 kalau bit
    pertamanya 1; kalau ada leading zero, nilainya pasti > 0. Jadi cukup lihat
    MSB byte pertama setelah NAL header — tidak perlu parsing penuh.
    """
    return len(nal) > 1 and (nal[1] & 0x80) != 0


def group_access_units(nals: list[bytes]) -> list[tuple[list[bytes], list[bytes]]]:
    """Gabungkan NAL jadi access unit: satu frame = satu paket (seperti MediaCodec).

    Ini krusial. Encoder bisa memecah satu frame jadi beberapa slice NAL; kalau
    tiap NAL dikirim sebagai paket sendiri, decoder menganggap tiap slice sebagai
    keyframe terpisah dan langsung melempar "Decoding error".

    Hasil: daftar (parameter_set, frame). parameter_set = SPS/PPS untuk config
    packet; frame = sisa NAL satu picture.
    """
    units: list[list[bytes]] = []
    pending: list[bytes] = []          # NAL non-VCL yang menunggu picture berikutnya
    current: list[bytes] | None = None

    def flush() -> None:
        nonlocal current
        if current:
            units.append(current)
        current = None

    for nal in nals:
        kind = nal[0] & 0x1F
        if kind in NON_VCL:
            # parameter set / SEI / AUD selalu berada di depan sebuah picture
            flush()
            pending.append(nal)
            continue

        if current is None or (kind in (1, 5) and _starts_new_picture(nal)):
            flush()
            current = list(pending)
            pending.clear()
        current.append(nal)

    flush()

    result: list[tuple[list[bytes], list[bytes]]] = []
    for unit in units:
        config = [n for n in unit if (n[0] & 0x1F) in PARAM_NALS]
        frame = [n for n in unit if (n[0] & 0x1F) not in PARAM_NALS]
        if frame:
            result.append((config, frame))
    return result


def _header(pts_and_flags: int, size: int) -> bytes:
    return pts_and_flags.to_bytes(8, "big") + size.to_bytes(4, "big")


class MockSession(SubscriberHub):
    """Meniru antarmuka DeviceSession, tapi sumbernya berkas lokal."""

    def __init__(self, h264_path: Path = DEMO_H264, meta_path: Path = DEMO_META):
        super().__init__()
        self.h264_path = h264_path
        self.meta_path = meta_path
        self.width = 720
        self.height = 1280
        self.fps = 30
        self.device_name = "Demo Device (720x1280)"
        self.options: dict[str, object] = dict(config.DEFAULT_OPTIONS)
        self.running = False
        self.error: str | None = None
        self.started_at = 0.0
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._log: list[str] = []
        self.serial = "demo"

    # -- meta --------------------------------------------------------------
    def to_json(self) -> dict:
        return {
            "serial": "demo",
            "name": self.device_name,
            "running": self.running,
            "error": self.error,
            "port": 0,
            "bytes": self.bytes_sent,
            "clients": self.clients,
            "uptime": round(time.time() - self.started_at, 1) if self.started_at else 0,
            "options": self.options,
            "demo": True,
            "screen": f"{self._sniff.width}x{self._sniff.height}" if self._sniff.width else f"{self.width}x{self.height}",
            "log": self._log[-25:],
        }

    def send_control(self, data: bytes) -> bool:
        if data:
            self._log.append(f"{time.strftime('%H:%M:%S')} kontrol {len(data)}B (demo: diabaikan)")
            del self._log[:-25]
        return True

    def inject(self, name: str, **kwargs) -> bool:
        return True

    # -- pub/sub ----------------------------------------------------------
    def subscribe(self) -> tuple[int, queue.Queue]:
        if not self.running:
            self.start()
        return super().subscribe()

    def request_keyframe(self) -> None:
        """Demo: tidak ada device untuk diperintah.

        Klien baru cukup menunggu IDR berikutnya. Berkas demo punya keyframe tiap
        ~1 detik, jadi jedanya singkat. Sengaja TIDAK memutar ulang berkas dari
        awal: itu akan mengirim codec id kedua di tengah stream dan merusak parser
        klien yang sudah jalan.
        """

    # -- lifecycle --------------------------------------------------------
    def start(self) -> None:
        if self.running:
            return
        if not self.h264_path.is_file():
            self.error = f"aset demo belum ada: {self.h264_path} (jalankan tools/make_demo.py)"
            return
        if self.meta_path.is_file():
            try:
                meta = json.loads(self.meta_path.read_text(encoding="utf-8"))
                self.width = int(meta.get("width", self.width))
                self.height = int(meta.get("height", self.height))
                self.fps = float(meta.get("fps", self.fps)) or 30.0
                self.device_name = f"Demo Device ({self.width}x{self.height})"
            except (ValueError, OSError):
                pass

        self._stop.clear()
        self.running = True
        self.started_at = time.time()
        self._thread = threading.Thread(target=self._play, name="demo-player", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self.running = False
        self.reset_stream()
        self.close_subscribers()

    def _play(self) -> None:
        data = self.h264_path.read_bytes()
        units = group_access_units(split_annex_b(data))
        if not units:
            self.error = "berkas demo tidak berisi akses unit"
            self.running = False
            return

        # pembuka stream: codec id (4 byte) lalu session packet (flags, width, height)
        self._publish(CODEC_ID_H264.to_bytes(4, "big"))
        session_flags = (PACKET_FLAG_SESSION >> 32).to_bytes(4, "big")
        self._publish(session_flags + self.width.to_bytes(4, "big") + self.height.to_bytes(4, "big"))

        fps = float(self.fps) or 30.0
        frame_ns = int(1_000_000 / fps)
        pts = 0

        try:
            while not self._stop.is_set():
                for config, frame in units:
                    if self._stop.is_set():
                        return

                    if config:
                        payload = b"".join(b"\x00\x00\x00\x01" + n for n in config)
                        self._publish(_header(PACKET_FLAG_CONFIG, len(payload)) + payload)

                    payload = b"".join(b"\x00\x00\x00\x01" + n for n in frame)
                    is_key = any((n[0] & 0x1F) == 5 for n in frame)
                    flags = pts | (PACKET_FLAG_KEY_FRAME if is_key else 0)
                    self._publish(_header(flags, len(payload)) + payload)

                    pts += frame_ns
                    time.sleep(1.0 / fps)
        except Exception as exc:  # pragma: no cover - hanya untuk diagnosa
            self.error = f"demo gagal: {exc}"
        finally:
            self.running = False
