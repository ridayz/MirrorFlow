"""Implementasi minimal protokol WebSocket (RFC 6455) pakai stdlib saja.

Cukup untuk kebutuhan kita: teks (JSON) dua arah dan binary satu arah (video).
Tidak menangani ekstensi (permessage-deflate) dan tidak menangani subprotocol.
"""

from __future__ import annotations

import base64
import hashlib
import struct
import threading

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

MAX_PAYLOAD = 1 << 24  # 16 MB, pengaman


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


class WSConnection:
    def __init__(self, rfile, wfile):
        self.rfile = rfile
        self.wfile = wfile
        self._write_lock = threading.Lock()
        self.closed = False

    # -- baca -------------------------------------------------------------
    def _read_exact(self, count: int) -> bytes | None:
        data = self.rfile.read(count)
        if not data or len(data) < count:
            return None
        return data

    def recv(self) -> tuple[int, bytes] | None:
        """Ambil satu pesan utuh. None kalau koneksi ditutup."""
        fragments = bytearray()
        message_opcode = 0
        while True:
            head = self._read_exact(2)
            if head is None:
                return None
            first, second = head[0], head[1]
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                extra = self._read_exact(2)
                if extra is None:
                    return None
                length = struct.unpack(">H", extra)[0]
            elif length == 127:
                extra = self._read_exact(8)
                if extra is None:
                    return None
                length = struct.unpack(">Q", extra)[0]

            if length > MAX_PAYLOAD:
                self.close(1009, "payload terlalu besar")
                return None

            mask_key = b""
            if masked:
                mask_key = self._read_exact(4)
                if mask_key is None:
                    return None

            payload = b"" if length == 0 else self._read_exact(length)
            if payload is None:
                return None
            if masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))

            if opcode == OP_CLOSE:
                self.close()
                return None
            if opcode == OP_PING:
                self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue

            if opcode == OP_CONT:
                if not message_opcode:
                    continue
                fragments += payload
            else:
                message_opcode = opcode
                fragments = bytearray(payload)

            if fin:
                return message_opcode, bytes(fragments)

    # -- tulis ------------------------------------------------------------
    def _send(self, opcode: int, payload: bytes) -> bool:
        if self.closed:
            return False
        length = len(payload)
        header = bytearray([0x80 | opcode])
        if length < 126:
            header.append(length)
        elif length < (1 << 16):
            header.append(126)
            header += struct.pack(">H", length)
        else:
            header.append(127)
            header += struct.pack(">Q", length)
        try:
            with self._write_lock:
                self.wfile.write(bytes(header))
                if payload:
                    self.wfile.write(payload)
            return True
        except (OSError, ValueError):
            self.closed = True
            return False

    def send_text(self, text: str) -> bool:
        return self._send(OP_TEXT, text.encode("utf-8"))

    def send_binary(self, payload: bytes) -> bool:
        return self._send(OP_BINARY, payload)

    def close(self, code: int = 1000, reason: str = "") -> None:
        if self.closed:
            return
        body = struct.pack(">H", code) + reason.encode("utf-8")
        self._send(OP_CLOSE, body)
        self.closed = True
