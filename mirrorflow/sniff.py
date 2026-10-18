"""Pengintip framing scrcpy di sisi Python.

Tugasnya: memecah aliran byte jadi paket-paket utuh, sambil melacak codec id,
session packet (ukuran layar), dan config packet (SPS/PPS). Stream aslinya
tidak diubah — hanya dipecah.

Format (big-endian, scrcpy v5.0.1 Streamer.java):
    [codec id u32]                    0x68323634 = "h264"
    lalu berulang:
      session packet: byte0 MSB = 1 -> flags u32, width u32, height u32
      media packet:   byte0 MSB = 0 -> ptsAndFlags u64, size u32, payload
        bit62 = config packet (SPS/PPS), bit61 = key frame
"""

from __future__ import annotations

KIND_CODEC = "codec"
KIND_SESSION = "session"
KIND_CONFIG = "config"
KIND_KEY = "key"
KIND_FRAME = "frame"

MAX_PACKET = 1 << 25  # 32 MB: penjaga kalau buffer melenceng


class StreamSniffer:
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._buf = bytearray()
        self.codec: bytes | None = None
        self.session: bytes | None = None
        self.config: bytes | None = None
        self.width = 0
        self.height = 0

    def feed(self, chunk: bytes) -> list[tuple[str, bytes]]:
        """Kembalikan paket-paket utuh yang baru masuk; sisanya disimpan."""
        self._buf += chunk
        buf = self._buf
        offset = 0
        total = len(buf)
        packets: list[tuple[str, bytes]] = []

        while True:
            if self.codec is None:
                if total - offset < 4:
                    break
                self.codec = bytes(buf[offset:offset + 4])
                offset += 4
                packets.append((KIND_CODEC, self.codec))
                continue

            if total - offset < 12:
                break

            head = buf[offset]
            if head & 0x80:  # session packet
                self.session = bytes(buf[offset:offset + 12])
                self.width = int.from_bytes(buf[offset + 4:offset + 8], "big")
                self.height = int.from_bytes(buf[offset + 8:offset + 12], "big")
                offset += 12
                packets.append((KIND_SESSION, self.session))
                continue

            size = int.from_bytes(buf[offset + 8:offset + 12], "big")
            if size > MAX_PACKET:
                # aliran tidak sinkron: buang semua, tunggu sesi berikutnya
                self.reset()
                return packets
            if total - offset - 12 < size:
                break

            packet = bytes(buf[offset:offset + 12 + size])
            if head & 0x40:
                self.config = packet
                kind = KIND_CONFIG
            elif head & 0x20:
                kind = KIND_KEY
            else:
                kind = KIND_FRAME
            packets.append((kind, packet))
            offset += 12 + size

        del self._buf[:offset]
        return packets

    def header(self) -> bytes:
        """Preroll untuk klien baru: codec id, ukuran layar, dan SPS/PPS."""
        parts: list[bytes] = []
        if self.codec:
            parts.append(self.codec)
        if self.session:
            parts.append(self.session)
        if self.config:
            parts.append(self.config)
        return b"".join(parts)
