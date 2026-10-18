"""Lokasi tool, path aset, dan nilai default."""

from __future__ import annotations

import os
import shutil
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor"
ASSETS = ROOT / "assets"
PACKAGE = Path(__file__).resolve().parent
STATIC = PACKAGE / "static"

# Versi harus SAMA PERSIS dengan scrcpy-server, kalau tidak server menolak jalan.
SCRCPY_VERSION = "5.0.1"
SCRCPY_SERVER_JAR = VENDOR / f"scrcpy-server-v{SCRCPY_VERSION}.jar"
SERVER_JAR_REMOTE = "/data/local/tmp/scrcpy-server.jar"

HOST = "127.0.0.1"
HTTP_PORT = 27190

# Parameter awal yang dikirim ke scrcpy-server. Semuanya bisa diubah dari UI.
DEFAULT_OPTIONS: dict[str, object] = {
    "max_size": 0,          # 0 = resolusi native
    "max_fps": 0,           # 0 = sebisa device
    "video_bit_rate": 8_000_000,
    "stay_awake": True,
    "show_touches": False,
}


def _first_file(*candidates: str | None) -> str | None:
    for cand in candidates:
        if cand and Path(cand).is_file():
            return str(Path(cand))
    return None


def find_adb() -> str | None:
    """Cari adb.exe: env ADB -> PATH -> winget -> Android SDK standar."""
    cands: list[str | None] = [os.environ.get("ADB"), shutil.which("adb")]

    local = os.environ.get("LOCALAPPDATA")
    if local:
        cands.append(str(Path(local) / "Microsoft" / "WinGet" / "Links" / "adb.exe"))
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            cands += [str(p) for p in sorted(packages.glob("Google.PlatformTools*/platform-tools/adb.exe"))]
        cands.append(str(Path(local) / "Android" / "Sdk" / "platform-tools" / "adb.exe"))

    for var in ("ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        base = os.environ.get(var)
        if base:
            cands.append(str(Path(base) / "platform-tools" / "adb.exe"))

    return _first_file(*cands)


def find_ffmpeg() -> str | None:
    """ffmpeg dipakai untuk membuat aset demo dan (nanti) rekaman."""
    cands: list[str | None] = [os.environ.get("FFMPEG"), shutil.which("ffmpeg")]
    local = os.environ.get("LOCALAPPDATA")
    if local:
        packages = Path(local) / "Microsoft" / "WinGet" / "Packages"
        if packages.is_dir():
            cands += [str(p) for p in sorted(packages.glob("Gyan.FFmpeg*/**/bin/ffmpeg.exe"))]
    return _first_file(*cands)


def free_port(host: str = HOST) -> int:
    """Ambil port TCP bebas dari OS (hindari bentrok 27183 bawaan scrcpy)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])
