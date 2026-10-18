"""Encoder pesan kontrol (client -> device).

Byte layout diambil apa adanya dari scrcpy v5.0.1:
  app/src/control_msg.c  (sc_control_msg_serialize)
  app/src/control_msg.h  (enum sc_control_msg_type)
  app/src/mouse_sdk.c    (semantik event mouse)
Semua integer big-endian.
"""

from __future__ import annotations

import struct

# --- tipe pesan ------------------------------------------------------------
TYPE_INJECT_KEYCODE = 0
TYPE_INJECT_TEXT = 1
TYPE_INJECT_TOUCH_EVENT = 2
TYPE_INJECT_SCROLL_EVENT = 3
TYPE_BACK_OR_SCREEN_ON = 4
TYPE_EXPAND_NOTIFICATION_PANEL = 5
TYPE_EXPAND_SETTINGS_PANEL = 6
TYPE_COLLAPSE_PANELS = 7
TYPE_GET_CLIPBOARD = 8
TYPE_SET_CLIPBOARD = 9
TYPE_SET_DISPLAY_POWER = 10
TYPE_ROTATE_DEVICE = 11
TYPE_UHID_CREATE = 12
TYPE_UHID_INPUT = 13
TYPE_UHID_DESTROY = 14
TYPE_OPEN_HARD_KEYBOARD_SETTINGS = 15
TYPE_START_APP = 16
TYPE_RESET_VIDEO = 17
TYPE_CAMERA_SET_TORCH = 18
TYPE_CAMERA_ZOOM_IN = 19
TYPE_CAMERA_ZOOM_OUT = 20
TYPE_RESIZE_DISPLAY = 21
TYPE_SCAN_FILE = 22

# --- action ---------------------------------------------------------------
ACTION_DOWN = 0
ACTION_UP = 1
ACTION_MOVE = 2
ACTION_CANCEL = 3
ACTION_OUTSIDE = 4
ACTION_POINTER_DOWN = 5
ACTION_POINTER_UP = 6
ACTION_HOVER_MOVE = 7
ACTION_SCROLL = 8
ACTION_HOVER_ENTER = 9
ACTION_HOVER_EXIT = 10
ACTION_BUTTON_PRESS = 11
ACTION_BUTTON_RELEASE = 12

# --- tombol mouse -> AMOTION_EVENT_BUTTON_* -------------------------------
BUTTON_PRIMARY = 1
BUTTON_SECONDARY = 2
BUTTON_TERTIARY = 4
BUTTON_BACK = 8
BUTTON_FORWARD = 16

SDL_TO_ANDROID_BUTTON = {
    0: BUTTON_PRIMARY,    # kiri
    1: BUTTON_TERTIARY,   # tengah
    2: BUTTON_SECONDARY,  # kanan
    3: BUTTON_BACK,       # X1
    4: BUTTON_FORWARD,    # X2
}

# id khusus; nilai negatif direpresentasikan sebagai u64
POINTER_MOUSE = 0xFFFFFFFFFFFFFFFF
POINTER_GENERIC_FINGER = 0xFFFFFFFFFFFFFFFE
POINTER_VIRTUAL_FINGER = 0xFFFFFFFFFFFFFFFD

INJECT_TEXT_MAX_LENGTH = 300
CLIPBOARD_TEXT_MAX_LENGTH = (1 << 18) - 14
SCAN_FILE_PATH_MAX_LENGTH = 256


def _u16fp(value: float) -> int:
    """float [0,1] -> u16 fixed point (sc_float_to_u16fp)."""
    return max(0, min(0xFFFF, int(value * 65536.0)))


def _i16fp(value: float) -> int:
    """float [-1,1] -> i16 fixed point (sc_float_to_i16fp)."""
    return max(-0x8000, min(0x7FFF, int(value * 32768.0)))


def _position(x: int, y: int, width: int, height: int) -> bytes:
    return struct.pack(">iiHH", int(x), int(y), int(width) & 0xFFFF, int(height) & 0xFFFF)


def touch(
    action: int,
    x: int,
    y: int,
    width: int,
    height: int,
    *,
    pressure: float = 1.0,
    action_button: int = 0,
    buttons: int = 0,
    pointer_id: int = POINTER_MOUSE,
) -> bytes:
    """32 byte: type, action, pointer_id(u64), x, y, w, h, pressure, action_button, buttons."""
    return (
        struct.pack(">BBQ", TYPE_INJECT_TOUCH_EVENT, action, pointer_id)
        + _position(x, y, width, height)
        + struct.pack(">Hii", _u16fp(pressure), action_button, buttons)
    )


def keycode(action: int, code: int, repeat: int = 0, metastate: int = 0) -> bytes:
    """14 byte: type, action, keycode(u32), repeat(u32), metastate(u32)."""
    return struct.pack(">BBIII", TYPE_INJECT_KEYCODE, action, code, repeat, metastate)


def text(value: str) -> bytes:
    payload = value.encode("utf-8")[:INJECT_TEXT_MAX_LENGTH]
    return struct.pack(">BI", TYPE_INJECT_TEXT, len(payload)) + payload


def scroll(x: int, y: int, width: int, height: int, hscroll: float, vscroll: float, buttons: int = 0) -> bytes:
    """21 byte. hscroll/vscroll dinormalisasi ke [-1,1] seperti scrcpy."""
    hnorm = max(-1.0, min(1.0, hscroll / 16.0))
    vnorm = max(-1.0, min(1.0, vscroll / 16.0))
    return (
        struct.pack(">B", TYPE_INJECT_SCROLL_EVENT)
        + _position(x, y, width, height)
        + struct.pack(">hhi", _i16fp(hnorm), _i16fp(vnorm), buttons)
    )


def back_or_screen_on(action: int) -> bytes:
    return struct.pack(">BB", TYPE_BACK_OR_SCREEN_ON, action)


def set_display_power(on: bool) -> bytes:
    return struct.pack(">BB", TYPE_SET_DISPLAY_POWER, 1 if on else 0)


def set_clipboard(sequence: int, value: str, paste: bool = False) -> bytes:
    payload = value.encode("utf-8")[:CLIPBOARD_TEXT_MAX_LENGTH]
    return (
        struct.pack(">BQB", TYPE_SET_CLIPBOARD, sequence & 0xFFFFFFFFFFFFFFFF, 1 if paste else 0)
        + struct.pack(">I", len(payload))
        + payload
    )


def get_clipboard(copy_key: int = 1) -> bytes:
    return struct.pack(">BB", TYPE_GET_CLIPBOARD, copy_key)


def scan_file(path: str) -> bytes:
    payload = path.encode("utf-8")[:SCAN_FILE_PATH_MAX_LENGTH]
    return struct.pack(">BI", TYPE_SCAN_FILE, len(payload)) + payload


def _simple(msg_type: int) -> bytes:
    return struct.pack(">B", msg_type)


def expand_notification_panel() -> bytes:
    return _simple(TYPE_EXPAND_NOTIFICATION_PANEL)


def expand_settings_panel() -> bytes:
    return _simple(TYPE_EXPAND_SETTINGS_PANEL)


def collapse_panels() -> bytes:
    return _simple(TYPE_COLLAPSE_PANELS)


def rotate_device() -> bytes:
    return _simple(TYPE_ROTATE_DEVICE)


def reset_video() -> bytes:
    return _simple(TYPE_RESET_VIDEO)


def open_hard_keyboard_settings() -> bytes:
    return _simple(TYPE_OPEN_HARD_KEYBOARD_SETTINGS)
