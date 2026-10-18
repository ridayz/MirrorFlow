"""Distribusi stream ke banyak klien, dengan penyaringan sampai keyframe.

Kenapa perlu: klien yang menyambung di tengah GOP hanya akan menerima delta
frame yang referensinya tidak pernah dia lihat. Decoder (WebCodecs) langsung
melempar "Decoding error" dan mati. Jadi:

  - setiap pelanggan baru menerima "preroll" (codec id + session packet + SPS/PPS);
  - frame biasa tidak diteruskan sampai keyframe pertama lewat;
  - subclass diminta memaksa device mengirim keyframe baru (reset_video).
"""

from __future__ import annotations

import itertools
import queue
import threading

from .sniff import (
    KIND_CODEC,
    KIND_CONFIG,
    KIND_FRAME,
    KIND_KEY,
    KIND_SESSION,
    StreamSniffer,
)

# paket yang selalu diteruskan (idempoten, tidak butuh referensi frame)
ALWAYS = frozenset({KIND_CODEC, KIND_SESSION, KIND_CONFIG})


class _Subscriber:
    __slots__ = ("queue", "needs_key", "dropped")

    def __init__(self, size: int):
        self.queue: queue.Queue = queue.Queue(maxsize=size)
        self.needs_key = True
        self.dropped = 0


class SubscriberHub:
    """Bagian pub/sub yang dipakai bersama oleh sesi device dan sesi demo."""

    def __init__(self, queue_size: int = 240):
        self._sniff = StreamSniffer()
        self._subs: dict[int, _Subscriber] = {}
        self._counter = itertools.count(1)
        self._hub_lock = threading.Lock()
        self._queue_size = queue_size
        self.bytes_sent = 0
        self.clients = 0

    # -- pub/sub ----------------------------------------------------------
    def subscribe(self) -> tuple[int, queue.Queue]:
        token = next(self._counter)
        sub = _Subscriber(self._queue_size)
        with self._hub_lock:
            self._subs[token] = sub
            self.clients = len(self._subs)

        preroll = self._sniff.header()
        if preroll:
            sub.queue.put_nowait(preroll)

        # kalau stream sudah jalan, klien ini masuk di tengah GOP -> minta keyframe
        if self.stream_started():
            self.request_keyframe()
        return token, sub.queue

    def unsubscribe(self, token: int) -> None:
        with self._hub_lock:
            self._subs.pop(token, None)
            self.clients = len(self._subs)

    def stream_started(self) -> bool:
        return self._sniff.session is not None

    def request_keyframe(self) -> None:
        """Override di subclass: paksa device mengirim IDR baru."""

    def _publish(self, chunk: bytes) -> None:
        self.bytes_sent += len(chunk)
        packets = self._sniff.feed(chunk)
        if not packets:
            return

        with self._hub_lock:
            subs = list(self._subs.values())
        if not subs:
            return

        for kind, raw in packets:
            always = kind in ALWAYS
            for sub in subs:
                if not always and sub.needs_key:
                    if kind != KIND_KEY:
                        sub.dropped += 1
                        continue
                    sub.needs_key = False
                _offer(sub.queue, raw)

    def close_subscribers(self) -> None:
        """Kirim penanda EOF supaya thread pengirim berhenti."""
        with self._hub_lock:
            subs = list(self._subs.values())
        for sub in subs:
            _offer(sub.queue, None)

    def reset_stream(self) -> None:
        self._sniff.reset()
        with self._hub_lock:
            for sub in self._subs.values():
                sub.needs_key = True


def _offer(q: queue.Queue, item) -> None:
    try:
        q.put_nowait(item)
    except queue.Full:
        # pembaca lambat: buang yang tertua supaya latensi tidak menumpuk
        try:
            q.get_nowait()
            q.put_nowait(item)
        except (queue.Empty, queue.Full):
            pass
