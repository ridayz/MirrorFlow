"""Entry point: `python -m mirrorflow [opsi]`."""

from __future__ import annotations

import argparse

from . import __version__, config
from .server import serve


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mirrorflow",
        description="Screen mirror Android ke PC dengan UI web modern (protokol scrcpy).",
    )
    parser.add_argument("--host", default=config.HOST, help=f"alamat bind (default {config.HOST})")
    parser.add_argument("--port", type=int, default=config.HTTP_PORT,
                        help=f"port UI (default {config.HTTP_PORT}, otomatis naik kalau terpakai)")
    parser.add_argument("--demo", action="store_true",
                        help="jalankan device demo (memutar berkas H.264 lokal) tanpa HP")
    parser.add_argument("--no-browser", action="store_true", help="jangan buka browser otomatis")
    parser.add_argument("--version", action="version", version=f"MirrorFlow {__version__}")
    args = parser.parse_args(argv)

    serve(host=args.host, port=args.port, demo=args.demo, open_browser=not args.no_browser)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
