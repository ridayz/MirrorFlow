/**
 * Pemetaan input PC -> pesan kontrol scrcpy.
 *
 * Semantiknya mengikuti scrcpy (app/src/mouse_sdk.c):
 *   - klik kiri   -> touch DOWN / MOVE / UP, pointer id = MOUSE
 *   - klik kanan  -> BACK
 *   - klik tengah -> HOME
 *   - scroll      -> INJECT_SCROLL_EVENT
 *   - tuts        -> KEYCODE bila dikenal, kalau tidak INJECT_TEXT
 */

const ACTION_DOWN = 0;
const ACTION_UP = 1;
const ACTION_MOVE = 2;
const ACTION_HOVER_MOVE = 7;

const BUTTON_PRIMARY = 1;
const BUTTON_SECONDARY = 2;
const BUTTON_TERTIARY = 4;
const BUTTON_BACK = 8;
const BUTTON_FORWARD = 16;

/** index tombol pointer (DOM) -> flag AMOTION_EVENT_BUTTON_* */
const DOM_BUTTONS = { 0: BUTTON_PRIMARY, 1: BUTTON_TERTIARY, 2: BUTTON_SECONDARY, 3: BUTTON_BACK, 4: BUTTON_FORWARD };

const AMETA_SHIFT_ON = 0x01;
const AMETA_ALT_ON = 0x02;
const AMETA_CTRL_ON = 0x1000;
const AMETA_META_ON = 0x10000;

export const AKEY = {
  HOME: 3,
  BACK: 4,
  DPAD_UP: 19,
  DPAD_DOWN: 20,
  DPAD_LEFT: 21,
  DPAD_RIGHT: 22,
  DPAD_CENTER: 23,
  VOLUME_UP: 24,
  VOLUME_DOWN: 25,
  POWER: 26,
  A: 29,
  TAB: 61,
  SPACE: 62,
  ENTER: 66,
  DEL: 67,
  GRAVE: 68,
  MINUS: 69,
  EQUALS: 70,
  LEFT_BRACKET: 71,
  RIGHT_BRACKET: 72,
  BACKSLASH: 73,
  SEMICOLON: 74,
  APOSTROPHE: 75,
  SLASH: 76,
  AT: 77,
  PLUS: 81,
  MENU: 82,
  PAGE_UP: 92,
  PAGE_DOWN: 93,
  ESCAPE: 111,
  FORWARD_DEL: 112,
  MOVE_HOME: 122,
  MOVE_END: 123,
  CAPS_LOCK: 115,
  NUM_LOCK: 143,
  SCROLL_LOCK: 116,
  VOLUME_MUTE: 164,
  APP_SWITCH: 187,
  COMMA: 55,
  PERIOD: 56,
};

/** event.code -> Android keycode */
const CODE_MAP = {
  Enter: AKEY.ENTER,
  NumpadEnter: AKEY.ENTER,
  Backspace: AKEY.DEL,
  Delete: AKEY.FORWARD_DEL,
  Escape: AKEY.ESCAPE,
  Tab: AKEY.TAB,
  Space: AKEY.SPACE,
  ArrowUp: AKEY.DPAD_UP,
  ArrowDown: AKEY.DPAD_DOWN,
  ArrowLeft: AKEY.DPAD_LEFT,
  ArrowRight: AKEY.DPAD_RIGHT,
  Home: AKEY.MOVE_HOME,
  End: AKEY.MOVE_END,
  PageUp: AKEY.PAGE_UP,
  PageDown: AKEY.PAGE_DOWN,
  Minus: AKEY.MINUS,
  Equal: AKEY.EQUALS,
  BracketLeft: AKEY.LEFT_BRACKET,
  BracketRight: AKEY.RIGHT_BRACKET,
  Backslash: AKEY.BACKSLASH,
  Semicolon: AKEY.SEMICOLON,
  Quote: AKEY.APOSTROPHE,
  Slash: AKEY.SLASH,
  Backquote: AKEY.GRAVE,
  Comma: AKEY.COMMA,
  Period: AKEY.PERIOD,
  CapsLock: AKEY.CAPS_LOCK,
  NumLock: AKEY.NUM_LOCK,
  ScrollLock: AKEY.SCROLL_LOCK,
};

for (let i = 0; i < 26; i++) CODE_MAP[`Key${String.fromCharCode(65 + i)}`] = AKEY.A + i;
for (let i = 0; i <= 9; i++) CODE_MAP[`Digit${i}`] = i === 0 ? 7 : 7 + i;
for (let i = 1; i <= 12; i++) CODE_MAP[`F${i}`] = 130 + i;

export class InputController {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {(name: string, args: object) => void} send
   */
  constructor(canvas, send) {
    this.canvas = canvas;
    this.send = send;
    this.width = 0;
    this.height = 0;
    this.hover = false;
    this.buttons = 0;
    this.pressed = false;
    this.active = false;
    this._pointers = new Map();
    this._nextFinger = 0;
    this._handlers = [];
  }

  setScreenSize(width, height) {
    this.width = width;
    this.height = height;
  }

  setHover(enabled) {
    this.hover = enabled;
  }

  attach() {
    if (this.active) return;
    this.active = true;

    const on = (target, type, fn, opts) => {
      target.addEventListener(type, fn, opts);
      this._handlers.push([target, type, fn]);
    };

    on(this.canvas, 'pointerdown', (e) => this._pointerDown(e));
    on(this.canvas, 'pointermove', (e) => this._pointerMove(e), { passive: false });
    on(window, 'pointerup', (e) => this._pointerUp(e));
    on(window, 'pointercancel', (e) => this._pointerUp(e));
    on(this.canvas, 'contextmenu', (e) => e.preventDefault());
    on(this.canvas, 'wheel', (e) => this._wheel(e), { passive: false });
    on(window, 'keydown', (e) => this._key(e, 'down'));
    on(window, 'keyup', (e) => this._key(e, 'up'));
    on(this.canvas, 'pointerleave', () => { this.pressed = false; this.buttons = 0; });
  }

  detach() {
    for (const [target, type, fn] of this._handlers) target.removeEventListener(type, fn);
    this._handlers = [];
    this.active = false;
  }

  /* ---------------- konversi koordinat ---------------- */

  _toDevice(event) {
    const rect = this.canvas.getBoundingClientRect();
    const x = Math.round(((event.clientX - rect.left) / rect.width) * this.width);
    const y = Math.round(((event.clientY - rect.top) / rect.height) * this.height);
    return {
      x: Math.max(0, Math.min(this.width - 1, x || 0)),
      y: Math.max(0, Math.min(this.height - 1, y || 0)),
    };
  }

  _touchArgs(action, point, { pressure = 1, actionButton = 0, buttons = 0, pointer = 'mouse' } = {}) {
    return { action, x: point.x, y: point.y, w: this.width, h: this.height, pressure, action_button: actionButton, buttons, pointer };
  }

  /* ---------------- pointer ---------------- */

  _pointerDown(event) {
    if (!this.width || !this.height) return;
    this.canvas.focus();
    event.preventDefault();

    if (event.pointerType === 'touch') {
      const id = this._nextFinger++;
      this._pointers.set(event.pointerId, id);
      this.send('touch', this._touchArgs(ACTION_DOWN, this._toDevice(event), {
        pressure: event.pressure || 1,
        pointer: 'finger',
      }));
      return;
    }

    const button = DOM_BUTTONS[event.button] ?? BUTTON_PRIMARY;

    if (event.button === 2) {           // kanan -> BACK (seperti scrcpy)
      this.send('key', { action: ACTION_DOWN, code: AKEY.BACK });
      this.send('key', { action: ACTION_UP, code: AKEY.BACK });
      return;
    }
    if (event.button === 1) {           // tengah -> HOME
      this.send('key', { action: ACTION_DOWN, code: AKEY.HOME });
      this.send('key', { action: ACTION_UP, code: AKEY.HOME });
      return;
    }

    this.buttons |= button;
    this.pressed = true;
    this.canvas.setPointerCapture?.(event.pointerId);
    this.send('touch', this._touchArgs(ACTION_DOWN, this._toDevice(event), {
      pressure: 1,
      actionButton: button,
      buttons: this.buttons,
    }));
  }

  _pointerMove(event) {
    if (!this.width || !this.height) return;

    if (event.pointerType === 'touch') {
      if (!this._pointers.has(event.pointerId)) return;
      event.preventDefault();
      this.send('touch', this._touchArgs(ACTION_MOVE, this._toDevice(event), {
        pressure: event.pressure || 1,
        pointer: 'finger',
      }));
      return;
    }

    const pressed = this.buttons !== 0;
    if (!pressed && !this.hover) return;
    event.preventDefault();
    this.send('touch', this._touchArgs(pressed ? ACTION_MOVE : ACTION_HOVER_MOVE, this._toDevice(event), {
      pressure: 1,
      buttons: this.buttons,
    }));
  }

  _pointerUp(event) {
    if (!this.width || !this.height) return;

    if (event.pointerType === 'touch') {
      if (!this._pointers.has(event.pointerId)) return;
      this._pointers.delete(event.pointerId);
      this.send('touch', this._touchArgs(ACTION_UP, this._toDevice(event), {
        pressure: 0,
        pointer: 'finger',
      }));
      return;
    }

    if (!this.pressed) return;
    const button = DOM_BUTTONS[event.button] ?? BUTTON_PRIMARY;
    this.buttons &= ~button;
    this.pressed = this.buttons !== 0;
    this.canvas.releasePointerCapture?.(event.pointerId);
    this.send('touch', this._touchArgs(ACTION_UP, this._toDevice(event), {
      pressure: 0,
      actionButton: button,
      buttons: this.buttons,
    }));
  }

  _wheel(event) {
    if (!this.width || !this.height) return;
    event.preventDefault();
    // vscroll dibalik karena sumbu SDL/Android berkebalikan dengan DOM
    this.send('scroll', {
      ...this._touchArgs(ACTION_MOVE, this._toDevice(event)),
      hscroll: event.deltaX / 60,
      vscroll: -event.deltaY / 60,
      buttons: this.buttons,
    });
  }

  /* ---------------- keyboard ---------------- */

  _key(event, phase) {
    const tag = (event.target?.tagName || '').toLowerCase();
    if (tag === 'input' || tag === 'select' || tag === 'textarea') return;
    if (!this.width) return;

    // pintasan bergaya scrcpy
    if (event.ctrlKey && phase === 'down' && this._shortcut(event)) return;

    const code = CODE_MAP[event.code];
    if (code !== undefined) {
      if (event.repeat && phase === 'down') return;
      event.preventDefault();
      this.send('key', {
        action: phase === 'down' ? ACTION_DOWN : ACTION_UP,
        code,
        meta: this._meta(event),
      });
      return;
    }

    if (phase === 'down' && event.key.length === 1 && !event.ctrlKey && !event.altKey && !event.metaKey) {
      event.preventDefault();
      this.send('text', { text: event.key });
    }
  }

  _shortcut(event) {
    const map = {
      KeyH: AKEY.HOME,
      KeyB: AKEY.BACK,
      KeyS: AKEY.APP_SWITCH,
      KeyM: AKEY.MENU,
      KeyP: AKEY.POWER,
      ArrowUp: AKEY.VOLUME_UP,
      ArrowDown: AKEY.VOLUME_DOWN,
    };
    const code = map[event.code];
    if (code !== undefined) {
      event.preventDefault();
      this.send('key', { action: ACTION_DOWN, code, meta: this._meta(event) });
      this.send('key', { action: ACTION_UP, code, meta: this._meta(event) });
      return true;
    }
    if (event.code === 'KeyO') {
      event.preventDefault();
      this.send('power', { on: false });
      return true;
    }
    return false;
  }

  _meta(event) {
    let meta = 0;
    if (event.shiftKey) meta |= AMETA_SHIFT_ON;
    if (event.altKey) meta |= AMETA_ALT_ON;
    if (event.ctrlKey) meta |= AMETA_CTRL_ON;
    if (event.metaKey) meta |= AMETA_META_ON;
    return meta;
  }

  /** Tombol toolbar: back / home / recents / rotate / power / panel. */
  tapKey(code) {
    this.send('key', { action: ACTION_DOWN, code });
    this.send('key', { action: ACTION_UP, code });
  }
}
