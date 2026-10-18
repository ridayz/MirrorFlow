"""Uji jalur data tanpa browser.

Menyambung ke /ws, minta attach ke sesi demo, lalu mem-parsing framing scrcpy
dengan implementasi terpisah. Kalau ini lolos, berarti kontrak
mock -> WebSocket -> parser sudah benar.

    python tools/check_stream.py [--port 27190] [--frames 60]
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import socket
import struct
import sys
import time

FLAG_SESSION = 1 << 63
FLAG_CONFIG = 1 << 62
FLAG_KEY = 1 << 61
CODEC_NAMES = {0x68323634: "h264", 0x68323635: "h265", 0x00617631: "av1"}


def handshake(host: str, port: int) -> socket.socket:
    sock = socket.create_connection((host, port), timeout=10.0)
    key = base64.b64encode(os.urandom(16)).decode()
    request = (
        f"GET /ws HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n\r\n"
    )
    sock.sendall(request.encode())

    buffer = b""
    while b"\r\n\r\n" not in buffer:
        chunk = sock.recv(4096)
        if not chunk:
            raise SystemExit("server menutup koneksi saat handshake")
        buffer += chunk
    head, _, rest = buffer.partition(b"\r\n\r\n")
    if b"101" not in head.split(b"\r\n")[0]:
        raise SystemExit(f"handshake gagal:\n{head.decode('utf-8', 'replace')}")
    return sock, rest


def send_text(sock: socket.socket, payload: dict) -> None:
    data = json.dumps(payload).encode()
    mask = os.urandom(4)
    header = bytearray([0x81])
    length = len(data)
    if length < 126:
        header.append(0x80 | length)
    elif length < (1 << 16):
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += mask
    sock.sendall(bytes(header) + bytes(b ^ mask[i & 3] for i, b in enumerate(data)))


class Reader:
    def __init__(self, sock: socket.socket, initial: bytes):
        self.sock = sock
        self.buf = bytearray(initial)

    def need(self, count: int) -> bytes | None:
        while len(self.buf) < count:
            chunk = self.sock.recv(65536)
            if not chunk:
                return None
            self.buf += chunk
        out = bytes(self.buf[:count])
        del self.buf[:count]
        return out

    def frame(self):
        head = self.need(2)
        if head is None:
            return None
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.need(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.need(8))[0]
        payload = self.need(length) if length else b""
        return opcode, payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=27190)
    parser.add_argument("--serial", default="demo")
    parser.add_argument("--frames", type=int, default=60)
    args = parser.parse_args()

    sock, rest = handshake(args.host, args.port)
    sock.settimeout(15.0)
    reader = Reader(sock, rest)

    opcode, payload = reader.frame()
    hello = json.loads(payload.decode())
    print(f"hello    : demo={hello.get('demo')} devices={len(hello.get('devices', []))} sessions={[s['serial'] for s in hello.get('sessions', [])]}")

    send_text(sock, {"t": "attach", "serial": args.serial})
    print(f"attach   : {args.serial}")

    stream = bytearray()
    codec = None
    width = height = 0
    media = config = keys = 0
    first_config = 0
    started = time.time()
    frames_ctx = None

    def fill() -> bool:
        """Tarik satu frame WS binary ke buffer stream."""
        nonlocal frames_ctx, media, config, keys, width, height, codec
        while True:
            item = reader.frame()
            if item is None:
                return False
            op, data = item
            if op == 0x2:
                stream.extend(data)
                return True
            if op == 0x1:
                msg = json.loads(data.decode())
                if msg.get("t") == "attached":
                    frames_ctx = msg
                    continue
                if msg.get("t") == "error":
                    print(f"error    : {msg}")
                    return False
                continue
            if op == 0x8:
                return False

    consumed = 0
    while media < args.frames and time.time() - started < 20:
        # codec id
        if codec is None:
            if len(stream) - consumed < 4 and not fill():
                break
            if len(stream) - consumed < 4:
                continue
            raw = int.from_bytes(stream[consumed:consumed + 4], "big")
            codec = CODEC_NAMES.get(raw, f"0x{raw:08x}")
            consumed += 4
            print(f"codec    : {codec}")
            continue

        # header 12 byte
        if len(stream) - consumed < 12 and not fill():
            break
        if len(stream) - consumed < 12:
            continue

        head = stream[consumed]
        if head & 0x80:
            width, height = int.from_bytes(stream[consumed + 4:consumed + 8], "big"), \
                            int.from_bytes(stream[consumed + 8:consumed + 12], "big")
            consumed += 12
            print(f"session  : {width}x{height}")
            continue

        size = int.from_bytes(stream[consumed + 8:consumed + 12], "big")
        while len(stream) - consumed - 12 < size:
            if not fill():
                break
        if len(stream) - consumed - 12 < size:
            break

        if head & 0x40:
            config += 1
            if config == 1:
                first_config = size
        else:
            media += 1
            if head & 0x20:
                keys += 1
        consumed += 12 + size

    elapsed = time.time() - started
    print(f"media    : {media} paket ({keys} key frame) dalam {elapsed:.1f}s -> {media / max(elapsed, 0.01):.1f} pps")
    print(f"config   : {config} paket (pertama {first_config} byte)")
    print(f"total    : {len(stream)} byte dari server")

    ok = codec == "h264" and width == 720 and height == 1280 and media >= min(args.frames, 10) and keys >= 1 and config >= 1
    print("HASIL    :", "LOLOS" if ok else "GAGAL")
    sock.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
