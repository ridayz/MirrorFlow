"""Buat aset demo (assets/demo.h264 + demo.json) memakai ffmpeg.

Dipanggil sekali saja:
    python tools/make_demo.py

Hasilnya dipakai mode `--demo` supaya seluruh jalur (framing -> WebSocket ->
WebCodecs -> canvas) bisa dites tanpa HP Android.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mirrorflow import config  # noqa: E402
from mirrorflow.mock import DEMO_H264, DEMO_META  # noqa: E402

WIDTH = 720
HEIGHT = 1280
FPS = 30
DURATION = 12


def main() -> int:
    ffmpeg = config.find_ffmpeg()
    if not ffmpeg:
        print("ffmpeg tidak ditemukan. Install: winget install Gyan.FFmpeg.Essentials")
        return 1

    DEMO_H264.parent.mkdir(parents=True, exist_ok=True)

    filtergraph = (
        f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS},"
        f"hue=H=2*PI*t/6:s=1,"
        "format=yuv420p"
    )

    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel", "error",
        "-y",
        "-f", "lavfi",
        "-i", filtergraph,
        "-t", str(DURATION),
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-tune", "zerolatency",
        "-profile:v", "baseline",
        "-level", "3.1",
        "-x264-params", f"keyint={FPS}:min-keyint={FPS}:scenecut=0:repeat-headers=1",
        "-f", "h264",
        str(DEMO_H264),
    ]

    print("menjalankan ffmpeg…")
    proc = subprocess.run(cmd)
    if proc.returncode != 0:
        print(f"ffmpeg gagal (kode {proc.returncode})")
        return proc.returncode

    DEMO_META.write_text(
        json.dumps(
            {"width": WIDTH, "height": HEIGHT, "fps": FPS, "duration": DURATION},
            indent=2,
        ),
        encoding="utf-8",
    )

    size = DEMO_H264.stat().st_size
    print(f"selesai: {DEMO_H264} ({size / 1024:.0f} KB)")
    print(f"meta   : {DEMO_META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
