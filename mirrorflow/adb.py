"""Pembungkus tipis untuk adb.exe."""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field

from .config import SERVER_JAR_REMOTE, find_adb

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


class AdbError(RuntimeError):
    pass


@dataclass
class Device:
    serial: str
    state: str
    model: str = ""
    product: str = ""
    device: str = ""
    transport_id: str = ""
    props: dict[str, str] = field(default_factory=dict)

    @property
    def online(self) -> bool:
        return self.state == "device"

    @property
    def label(self) -> str:
        return self.props.get("ro.product.model") or self.model or self.serial

    def to_json(self) -> dict:
        return {
            "serial": self.serial,
            "state": self.state,
            "online": self.online,
            "model": self.label,
            "brand": self.props.get("ro.product.brand", ""),
            "android": self.props.get("ro.build.version.release", ""),
            "sdk": self.props.get("ro.build.version.sdk", ""),
            "abi": self.props.get("ro.product.cpu.abi", ""),
        }


class Adb:
    def __init__(self, path: str | None = None):
        self.path = path or find_adb()
        if not self.path:
            raise AdbError("adb.exe tidak ditemukan. Install Android platform-tools dulu.")

    # -- eksekusi dasar ----------------------------------------------------
    def run(self, *args: str, timeout: float = 20.0, check: bool = True) -> subprocess.CompletedProcess:
        proc = subprocess.run(
            [self.path, *args],
            capture_output=True,
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        if check and proc.returncode != 0:
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
            raise AdbError(f"adb {' '.join(args)} gagal ({proc.returncode}): {err}")
        return proc

    def spawn(self, *args: str) -> subprocess.Popen:
        return subprocess.Popen(
            [self.path, *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=_CREATE_NO_WINDOW,
        )

    def shell(self, serial: str, *args: str, timeout: float = 20.0) -> str:
        proc = self.run("-s", serial, "shell", *args, timeout=timeout)
        return proc.stdout.decode("utf-8", "replace").strip()

    # -- device ------------------------------------------------------------
    def devices(self, with_props: bool = False) -> list[Device]:
        out = self.run("devices", "-l").stdout.decode("utf-8", "replace")
        result: list[Device] = []
        for line in out.splitlines()[1:]:
            line = line.strip()
            if not line or line.startswith("*"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            dev = Device(serial=parts[0], state=parts[1])
            for token in parts[2:]:
                if ":" not in token:
                    continue
                key, _, value = token.partition(":")
                if hasattr(dev, key):
                    setattr(dev, key, value)
            result.append(dev)

        if with_props:
            for dev in result:
                if dev.online:
                    try:
                        dev.props = self.props(dev.serial)
                    except AdbError:
                        pass
        return result

    def props(self, serial: str) -> dict[str, str]:
        """getprop -> dict. Hanya key yang kita perlukan supaya cepat."""
        keys = (
            "ro.product.model",
            "ro.product.brand",
            "ro.product.manufacturer",
            "ro.build.version.release",
            "ro.build.version.sdk",
            "ro.product.cpu.abi",
            "ro.serialno",
        )
        raw = self.shell(serial, "getprop", *keys, timeout=15.0)
        props: dict[str, str] = {}
        for key in keys:
            match = re.search(rf"\[{re.escape(key)}\]:\s*\[(.*?)\]\s*$", raw, re.MULTILINE)
            if match:
                props[key] = match.group(1)
        return props

    def screen_size(self, serial: str) -> tuple[int, int]:
        """Resolusi fisik layar dari `wm size`."""
        raw = self.shell(serial, "wm", "size")
        match = re.search(r"(\d+)x(\d+)", raw)
        if not match:
            return (0, 0)
        return (int(match.group(1)), int(match.group(2)))

    # -- transfer ----------------------------------------------------------
    def push(self, serial: str, local: str, remote: str) -> None:
        self.run("-s", serial, "push", local, remote, timeout=120.0)

    # -- port forward ------------------------------------------------------
    def forward(self, serial: str, local: str, remote: str) -> None:
        self.run("-s", serial, "forward", local, remote)

    def forward_remove(self, serial: str, local: str) -> None:
        self.run("-s", serial, "forward", "--remove", local, check=False)

    # -- server scrcpy -----------------------------------------------------
    def spawn_server(self, serial: str, version: str, options: dict[str, object]) -> subprocess.Popen:
        args = [
            "-s",
            serial,
            "shell",
            f"CLASSPATH={SERVER_JAR_REMOTE}",
            "app_process",
            "/",
            "com.genymobile.scrcpy.Server",
            version,
        ]
        args += [f"{key}={_fmt(value)}" for key, value in options.items()]
        return self.spawn(*args)


def _fmt(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)
