import { StreamRenderer } from './stream.js';
import { InputController, AKEY } from './control.js';

/* ---------------- elemen ---------------- */
const $ = (id) => document.getElementById(id);

const el = {
  canvas: $('screen'),
  shell: $('shell'),
  viewport: $('viewport'),
  overlay: $('overlay'),
  overlayTitle: $('overlay-title'),
  overlayText: $('overlay-text'),
  deviceList: $('device-list'),
  deviceHint: $('device-hint'),
  devCount: $('dev-count'),
  logList: $('log-list'),
  inspDevice: $('insp-device'),
  optFrame: $('opt-frame'),
  optMotion: $('opt-motion'),
  connPill: $('conn-pill'),
  version: $('version'),
  adbPath: $('adb-path'),
  devDetail: $('dev-detail'),
  statusDot: $('status-dot'),
  statusText: $('status-text'),
  logLine: $('log-line'),
  toast: $('toast'),
  statRes: $('stat-res'),
  statFps: $('stat-fps'),
  statNet: $('stat-net'),
  statCodec: $('stat-codec'),
  optSize: $('opt-size'),
  optFps: $('opt-fps'),
  optBitrate: $('opt-bitrate'),
  optBitrateValue: $('opt-bitrate-value'),
  optBitrateAuto: $('opt-bitrate-auto'),
  optAwake: $('opt-awake'),
  optTouches: $('opt-touches'),
  winTitle: $('win-title'),
  winSub: $('win-sub'),
  winTitle2: $('win-title-2'),
  winSub2: $('win-sub-2'),
};

/* ---------------- state ---------------- */
const state = {
  ws: null,
  connected: false,
  ready: false,          // sudah menerima state pertama dari server?
  devices: [],
  sessions: [],
  demo: false,
  selected: null,
  attached: null,
  paused: false,
  resolution: null,
  fps: 0,
  deviceInfo: null,
  reconnectDelay: 500,
};

/* ---------------- renderer + input ---------------- */
const renderer = new StreamRenderer(el.canvas, {
  onResolution: (w, h) => {
    state.resolution = { w, h };
    el.statRes.textContent = `${w}x${h}`;
    fitCanvas();
    input.setScreenSize(w, h);
    el.shell.classList.add('visible');
    el.overlay.classList.add('hidden');
    updateWindowChrome();
    updateBitrateUI();  // recalc auto bitrate on resolution change
  },
  onCodec: (codec) => {
    el.statCodec.textContent = codec;
    if (codec !== 'h264') toast(`codec ${codec} belum didukung`, true);
    updateWindowChrome();
  },
  onStats: ({ fps, mbps }) => {
    state.fps = fps;
    el.statFps.textContent = fps;
    el.statNet.textContent = mbps;
    updateWindowChrome();
  },
  onError: (message) => {
    el.logLine.textContent = message;
    if (/belum didukung|configure gagal|decoder/.test(message)) toast(message, true);
  },
});

const input = new InputController(el.canvas, (name, args) => {
  send({ t: 'command', serial: state.attached, name, args });
});
input.attach();

/* ---------------- websocket ---------------- */
function connect() {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws`);
  ws.binaryType = 'arraybuffer';
  state.ws = ws;

  ws.onopen = () => {
    state.connected = true;
    state.reconnectDelay = 500;
    setPill('online', 'on');
    setStatus('Terhubung ke MirrorFlow', 'on');
    // Kalau ini sambung ulang, pasang lagi ke sesi yang sedang dilihat.
    if (state.attached) {
      const serial = state.attached;
      renderer.reset();
      send({ t: 'attach', serial });
    }
  };

  ws.onmessage = (event) => {
    const data = event.data;
    if (typeof data === 'string') {
      handleMessage(JSON.parse(data));
    } else if (typeof Blob !== 'undefined' && data instanceof Blob) {
      // jaring pengaman: seharusnya binaryType sudah 'arraybuffer'
      data.arrayBuffer().then(feedVideo, () => {});
    } else {
      feedVideo(data);
    }
  };

  ws.onclose = () => {
    state.connected = false;
    setPill('offline', '');
    setStatus('Koneksi ke server terputus, mencoba lagi…', 'err');
    if (state.attached) renderer.reset();
    setTimeout(connect, state.reconnectDelay);
    state.reconnectDelay = Math.min(state.reconnectDelay * 1.6, 5000);
  };

  ws.onerror = () => setStatus('Galat koneksi WebSocket', 'err');
}

function send(payload) {
  if (state.ws?.readyState === WebSocket.OPEN) state.ws.send(JSON.stringify(payload));
}

/* Terima satu potongan byte dari socket video (selalu ArrayBuffer). */
function feedVideo(buffer) {
  if (state.paused) return;
  state.bytesIn = (state.bytesIn || 0) + buffer.byteLength;
  renderer.push(new Uint8Array(buffer));
}

/** Minta server mulai mengirim video; parser direset lebih dulu. */
function requestAttach(serial) {
  renderer.reset();
  state.attached = null;
  send({ t: 'attach', serial });
}

function handleMessage(data) {
  switch (data.t) {
    case 'hello':
    case 'state':
      if (data.version) el.version.textContent = `v${data.version}`;
      state.ready = true;
      state.devices = data.devices || [];
      state.sessions = data.sessions || [];
      state.demo = !!data.demo;
      el.adbPath.textContent = data.adb || (data.adbError ? 'tidak ada' : '—');
      if (data.adbError) el.adbPath.title = data.adbError;
      renderDevices();
      updateOverlay();
      updateWindowChrome();
      break;

    case 'attached':
      state.attached = data.serial;
      renderer.reset();
      el.shell.classList.add('visible');
      el.overlay.classList.add('hidden');
      setPill('mirroring', 'on');
      setStatus(`Menyiarkan ${data.session?.name || data.serial}`, 'on');
      renderDevices();
      updateWindowChrome();
      break;

    case 'detached':
      if (state.attached === data.serial) {
        state.attached = null;
        el.shell.classList.remove('visible');
      }
      renderDevices();
      updateOverlay();
      updateWindowChrome();
      break;

    case 'started':
      state.selected = data.session.serial;
      requestAttach(data.session.serial);
      renderDevices();
      break;

    case 'stopped':
      state.attached = null;
      el.shell.classList.remove('visible');
      renderer.reset();
      updateOverlay();
      updateWindowChrome();
      send({ t: 'refresh' });
      break;

    case 'error':
      toast(data.message, true);
      setStatus(data.message, 'err');
      break;
  }
}

/* ---------------- UI ---------------- */
function setPill(text, cls) {
  el.connPill.textContent = text;
  el.connPill.className = `pill ${cls}`;
}

/* Konsol gaya debug area Xcode: baris waktu + pesan, dipangkas agar tidak
   tumbuh tanpa batas. */
const LOG_LIMIT = 200;

function pushLog(message, kind) {
  if (!el.logList || !message) return;
  const row = document.createElement('div');
  row.className = `log-row${kind ? ` ${kind}` : ''}`;
  const stamp = document.createElement('span');
  stamp.className = 't';
  const now = new Date();
  stamp.textContent = [now.getHours(), now.getMinutes(), now.getSeconds()]
    .map((n) => String(n).padStart(2, '0')).join(':');
  const text = document.createElement('span');
  text.textContent = message;
  row.append(stamp, text);
  el.logList.appendChild(row);
  while (el.logList.childElementCount > LOG_LIMIT) el.logList.firstElementChild.remove();
  el.logList.scrollTop = el.logList.scrollHeight;
}

let lastStatus = null;

function setStatus(text, kind) {
  el.statusText.textContent = text;
  el.statusDot.className = `dot ${kind || ''}`;
  if (text === lastStatus) return;      // jangan spam konsol
  lastStatus = text;
  pushLog(text, kind === 'err' ? 'err' : kind === 'busy' ? 'info' : '');
}

/* Judul jendela ala Simulator: nama device + baris info di bawahnya. */
function updateWindowChrome() {
  const attached = state.attached;
  const session = state.sessions.find((s) => s.serial === attached);
  const device = state.devices.find((d) => d.serial === attached);

  const label = attached ? (session?.name || device?.model || attached) : null;

  const titleText = label || 'MirrorFlow';
  el.winTitle.textContent = titleText;
  if (el.winTitle2) el.winTitle2.textContent = titleText;
  if (el.inspDevice) el.inspDevice.textContent = label || '—';
  if (el.inspDevice) el.inspDevice.title = label || '';

  if (!attached) {
    const subText = state.demo ? 'Mode demo siap' : 'Belum ada sesi';
    el.winSub.textContent = subText;
    if (el.winSub2) el.winSub2.textContent = subText;
  } else {
    const parts = [];
    if (state.deviceInfo?.android) parts.push(`Android ${state.deviceInfo.android}`);
    if (state.resolution) parts.push(`${state.resolution.w} × ${state.resolution.h}`);
    if (state.fps) parts.push(`${state.fps} fps`);
    if (renderer.codec) parts.push(renderer.codec);
    const subText = parts.length ? parts.join('  ·  ') : 'Menyambung…';
    el.winSub.textContent = subText;
    if (el.winSub2) el.winSub2.textContent = subText;
  }

  document.body.classList.toggle('mirroring', !!attached);
}

let toastTimer = null;
function toast(message, isError = false) {
  el.toast.textContent = message;
  el.toast.className = `toast show${isError ? ' err' : ''}`;
  pushLog(message, isError ? 'err' : '');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.toast.className = 'toast'; }, 3200);
}

function initials(name) {
  return (name || '?').replace(/[^A-Za-z0-9]/g, '').slice(0, 2).toUpperCase() || '?';
}

function renderDevices() {
  const items = [...state.devices];

  // tampilkan device demo sebagai entri tersendiri
  const demoSession = state.sessions.find((s) => s.serial === 'demo');
  if (state.demo && demoSession) {
    items.unshift({ serial: 'demo', model: demoSession.name, brand: 'MirrorFlow', android: 'demo', online: true, state: 'demo' });
  }

  el.deviceList.innerHTML = '';

  if (!items.length) {
    el.devCount.textContent = '';
    el.deviceHint.textContent = el.adbPath.textContent === 'tidak ada'
      ? 'adb tidak ditemukan. Install Android platform-tools lalu muat ulang.'
      : 'Belum ada device. Colokkan HP lewat USB dan aktifkan USB debugging.';
    return;
  }

  el.devCount.textContent = String(items.length);
  el.deviceHint.textContent = `${items.length} device terdeteksi`;

  for (const device of items) {
    const li = document.createElement('li');
    li.className = 'device';
    const session = state.sessions.find((s) => s.serial === device.serial);
    if (state.attached === device.serial || state.selected === device.serial) li.classList.add('active');

    const subtitle = device.serial === 'demo'
      ? 'stream contoh'
      : `${device.serial}${device.android ? ` · Android ${device.android}` : ''}`;

    li.innerHTML = `
      <span class="avatar">${initials(device.model)}</span>
      <span class="info">
        <strong>${escapeHtml(device.model || device.serial)}</strong>
        <span>${escapeHtml(subtitle)}</span>
      </span>
      <span class="state ${device.online ? 'online' : ''}">${session?.running ? 'live' : escapeHtml(device.state || '')}</span>
    `;

    li.onclick = () => activate(device);
    el.deviceList.appendChild(li);
  }
}

function activate(device) {
  state.selected = device.serial;

  if (device.serial === 'demo') {
    send({ t: 'start', serial: 'demo', options: collectOptions() });
    return;
  }
  if (!device.online) {
    toast(`Device berstatus "${device.state}" — cek layar HP, mungkin minta izin USB debugging`, true);
    return;
  }

  const running = state.sessions.find((s) => s.serial === device.serial)?.running;
  if (running) {
    requestAttach(device.serial);
    return;
  }

  setStatus(`Menyiapkan ${device.model || device.serial}…`, 'busy');
  send({ t: 'start', serial: device.serial, options: collectOptions() });
  loadDeviceDetail(device.serial);
}

function updateOverlay() {
  const online = state.devices.filter((d) => d.online);
  const hasDemo = state.demo && state.sessions.some((s) => s.serial === 'demo');

  if (state.attached) {
    el.overlay.classList.add('hidden');
    return;
  }
  el.overlay.classList.remove('hidden');
  el.shell.classList.remove('visible');

  if (!online.length) {
    el.overlayTitle.textContent = state.demo && !hasDemo ? 'Mode demo tersedia' : 'Belum ada perangkat';
    el.overlayText.innerHTML = el.adbPath.textContent === 'tidak ada'
      ? 'Tool <b>adb</b> belum terpasang. Jalankan <b>winget install Google.PlatformTools</b> lalu buka ulang.'
      : 'Sambungkan HP Android lewat USB, aktifkan <b>Developer options</b> → <b>USB debugging</b>, lalu klik <b>Hubungkan</b>.';
  } else if (state.devices.every((d) => !d.online)) {
    el.overlayTitle.textContent = 'Menunggu izin device';
    el.overlayText.innerHTML = 'HP terdeteksi tapi belum diizinkan. Lihat layar HP dan setujui dialog <b>Allow USB debugging</b>.';
  } else {
    el.overlayTitle.textContent = 'Siap menyambung';
    el.overlayText.innerHTML = 'Pilih perangkat di panel kiri, atau klik <b>Hubungkan</b> untuk memakai perangkat pertama.';
  }
}

async function loadDeviceDetail(serial) {
  try {
    const res = await fetch(`/api/device/${encodeURIComponent(serial)}`);
    const data = await res.json();
    if (data.error) { el.devDetail.textContent = '—'; return; }
    state.deviceInfo = data;
    el.devDetail.textContent = `${data.model} · Android ${data.android} · ${data.screen}`;
    el.devDetail.title = `SDK ${data.sdk} · ${data.abi}`;
    updateWindowChrome();
  } catch {
    el.devDetail.textContent = '—';
  }
}

function collectOptions() {
  return {
    max_size: Number(el.optSize.value),
    max_fps: Number(el.optFps.value),
    video_bit_rate: Number(el.optBitrate.value) * 1_000_000,
    stay_awake: el.optAwake.checked,
    show_touches: el.optTouches.checked,
  };
}

/* ---------------- geometri kanvas + model device (adaptive) ---------------- */

/* Konstanta desain iPhone 16 Pro (rasio terhadap lebar layar) */
const IPHONE_FRAME_K = 0.03;          // tebal bezel
const IPHONE_ISLAND = { w: 0.27, h: 0.085, top: 0.028 };
const IPHONE_SIDE_H = 0.075;
const IPHONE_BODY_R = 0.128;
const IPHONE_SCREEN_R = 0.055;        // radius sudut layar di dalam bezel

const FIT_INSET = 34;                 // ruang bayangan di viewport

function fitCanvas() {
  if (!state.resolution) return;
  const { w, h } = state.resolution;  // native resolution (portrait for Samsung)

  const box = el.viewport.getBoundingClientRect();
  const availW = box.width - FIT_INSET * 2;
  const availH = box.height - FIT_INSET * 2;
  if (availW <= 40 || availH <= 40) return;

  const framed = !el.shell.classList.contains('no-frame');

  // First calculate display dimensions using native resolution
  const scale = Math.min(availW / w, availH / h);
  const displayW = Math.max(64, Math.round(w * scale));
  const displayH = Math.round(h * scale);

  el.canvas.style.width = `${displayW}px`;
  el.canvas.style.height = `${displayH}px`;

  let k, island, sideH, bodyR, screenR;

  if (framed) {
    /* Use the ACTUAL displayed aspect ratio for frame calculations.
       Compare to iPhone's SAME orientation aspect ratio for correct proportions. */
    const isLandscape = displayW > displayH;
    const IPHONE_AR = isLandscape ? (1179 / 2556) : (2556 / 1179);  // iPhone landscape ≈ 0.461, portrait ≈ 2.168
    const displayAR = displayH / displayW;
    const arRatio = displayAR / IPHONE_AR;
    
    k = IPHONE_FRAME_K * arRatio;
    island = {
      w: IPHONE_ISLAND.w * arRatio,
      h: IPHONE_ISLAND.h * arRatio,
      top: IPHONE_ISLAND.top * arRatio,
    };
    sideH = IPHONE_SIDE_H * arRatio;
    bodyR = IPHONE_BODY_R * arRatio;
    screenR = IPHONE_SCREEN_R * arRatio;
  } else {
    k = island = sideH = bodyR = screenR = 0;
  }

  const pad = Math.round(displayW * k);
  const radius = Math.round((displayW + pad * 2) * bodyR);
  const style = el.shell.style;

  style.setProperty('--phone-pad', `${pad}px`);
  style.setProperty('--phone-radius', `${radius}px`);
  style.setProperty('--screen-radius', `${Math.max(0, radius - pad)}px`);
  
  /* Dynamic Island: auto-hide di landscape (mirip iPhone asli) */
  const isLandscape = displayW > displayH;
  style.setProperty('--island-w', isLandscape ? '0px' : `${Math.round(displayW * island.w)}px`);
  style.setProperty('--island-h', isLandscape ? '0px' : `${Math.round(displayW * island.h)}px`);
  style.setProperty('--island-top', isLandscape ? '0px' : `${pad + Math.round(displayW * island.top)}px`);
  style.setProperty('--side-h', `${Math.round(displayW * sideH)}px`);
}

/* Ukuran area kanvas ikut berubah saat panel / konsol / bingkai di-toggle.
   Karena perubahannya dianimasikan, ukuran akhir baru terbaca setelah
   animasinya selesai — jadi pengukurannya ditunda dulu, lalu kanvasnya
   sendiri ikut beranimasi (kelas .morph) supaya tidak menyentak. */
let fitTimer = null;

function morphFit() {
  el.viewport.classList.add('morph');
  fitCanvas();
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => el.viewport.classList.remove('morph'), 400);
}

function scheduleFit(delay = 300) {
  el.viewport.classList.add('morph');
  clearTimeout(fitTimer);
  fitTimer = setTimeout(() => {
    fitCanvas();
    fitTimer = setTimeout(() => el.viewport.classList.remove('morph'), 400);
  }, delay);
}

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, (ch) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[ch]));
}

/* ---------------- event ---------------- */
$('btn-refresh').onclick = () => send({ t: 'refresh' });

/* run / stop ala Xcode */
$('btn-run').onclick = () => {
  const online = state.devices.find((d) => d.online && d.serial !== 'demo')
    || state.devices.find((d) => d.online);
  if (online) return activate(online);
  if (state.demo) return activate({ serial: 'demo' });
  toast('Tidak ada device online. Colokkan HP lalu muat ulang daftar.', true);
};

$('btn-stop').onclick = () => {
  if (!state.attached) return toast('Belum ada sesi aktif', true);
  send({ t: 'stop', serial: state.attached });
};

/* tampil / sembunyikan panel */
$('btn-nav-toggle').onclick = (event) => {
  document.querySelector('.workarea').classList.toggle('nav-hidden');
  event.currentTarget.classList.toggle('active');
  syncMenu();
  scheduleFit();
};
$('btn-insp-toggle').onclick = (event) => {
  document.querySelector('.cluster').classList.toggle('insp-hidden');
  event.currentTarget.classList.toggle('active');
  syncMenu();
  scheduleFit();
};
$('btn-console').onclick = () => {
  document.querySelector('.app').classList.toggle('console-hidden');
  syncMenu();
  scheduleFit(280);
};
$('btn-clear-log').onclick = () => {
  el.logList.innerHTML = '';
  el.logLine.textContent = '';
  pushLog('Konsol dibersihkan', 'info');
};

$('btn-demo').onclick = () => {
  if (!state.ready) return;      // state dari server belum sampai; tunggu saja
  if (!state.demo) return toast('Mode demo tidak aktif. Jalankan: python -m mirrorflow --demo', true);
  send({ t: 'start', serial: 'demo' });
};

$('btn-fullscreen').onclick = toggleFullscreen;

/* traffic light dipakai untuk hal yang paling sering dibutuhkan:
   merah = hentikan sesi, kuning = jeda, hijau = layar penuh. */
$('btn-close').onclick = () => {
  if (!state.attached) return toast('Belum ada sesi aktif', true);
  send({ t: 'stop', serial: state.attached });
};
$('btn-min').onclick = () => $('btn-pause').click();
$('btn-zoom').onclick = toggleFullscreen;

$('btn-shot').onclick = () => {
  if (!state.attached) return toast('Belum ada sesi aktif', true);
  el.canvas.toBlob((blob) => {
    if (!blob) return toast('Gagal menyimpan tangkapan layar', true);
    const name = String(state.attached).replace(/[^\w.-]+/g, '_');
    const stamp = new Date().toISOString().replace(/[:.]/g, '-').slice(0, 19);
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `mirrorflow-${name}-${stamp}.png`;
    link.click();
    setTimeout(() => URL.revokeObjectURL(url), 5000);
    toast('Tangkapan layar disimpan');
  }, 'image/png');
};
$('btn-pause').onclick = () => {
  state.paused = !state.paused;
  $('btn-pause').classList.toggle('active', state.paused);
  setStatus(state.paused ? 'Stream dijeda' : 'Stream dilanjutkan', state.paused ? 'busy' : 'on');
};

for (const button of document.querySelectorAll('.tool[data-cmd]')) {
  const cmd = button.dataset.cmd;
  button.onclick = () => {
    const target = state.attached;
    if (!target) return toast('Belum ada sesi aktif', true);
    if (cmd === 'back') input.tapKey(AKEY.BACK);
    else if (cmd === 'home') input.tapKey(AKEY.HOME);
    else if (cmd === 'recents') input.tapKey(AKEY.APP_SWITCH);
    else if (cmd === 'rotate') send({ t: 'command', serial: target, name: 'rotate', args: {} });
    else if (cmd === 'power') send({ t: 'command', serial: target, name: 'power', args: { on: false } });
    else if (cmd === 'notifications') send({ t: 'command', serial: target, name: 'notifications', args: {} });
    else if (cmd === 'settings') send({ t: 'command', serial: target, name: 'settings', args: {} });
  };
}

/* Auto bitrate: hitung optimal berdasarkan resolusi & FPS device */
function calcAutoBitrate() {
  if (!state.resolution) return 30;  // default fallback
  const { w, h } = state.resolution;
  const fps = Math.max(state.fps || 30, 25);  // asumsi minimal 25fps biar gak terlalu kecil
  const pixels = w * h;
  const mpixels = pixels / 1_000_000;
  // Formula: ~4 Mbps per MPixel @ 30fps, scale by fps
  // 2MP (1080p) -> 8 Mbps @ 30fps, 16 Mbps @ 60fps
  // 8MP (4K) -> 32 Mbps @ 30fps, 64 Mbps @ 60fps
  let mbps = Math.round(mpixels * 4 * (fps / 30));
  return Math.max(8, Math.min(80, mbps));  // clamp 8-80 Mbps
}

function updateBitrateUI() {
  const auto = el.optBitrateAuto?.checked;
  if (auto) {
    const mbps = calcAutoBitrate();
    el.optBitrate.value = mbps;
    el.optBitrateValue.textContent = `${mbps} Mbps (auto)`;
    el.optBitrate.disabled = true;
  } else {
    el.optBitrateValue.textContent = `${el.optBitrate.value} Mbps`;
    el.optBitrate.disabled = false;
  }
}

el.optBitrate.oninput = () => {
  if (!el.optBitrateAuto?.checked) {
    el.optBitrateValue.textContent = `${el.optBitrate.value} Mbps`;
  }
};

el.optBitrateAuto?.addEventListener('change', () => {
  updateBitrateUI();
  // persist preference
  try { localStorage.setItem('mirrorflow-bitrate-auto', el.optBitrateAuto.checked ? '1' : '0'); } catch {}
});

/* Init bitrate auto pref */
try {
  const saved = localStorage.getItem('mirrorflow-bitrate-auto');
  if (saved !== null) {
    el.optBitrateAuto.checked = saved === '1';
  }
} catch {}
updateBitrateUI();

el.canvas.ondblclick = toggleFullscreen;

/* Bingkai device iPhone bisa dimatikan: waktu mati, layar tampil apa adanya
   tanpa radius sudut dan tanpa Dynamic Island yang menutupi isi layar. */
const FRAME_KEY = 'mirrorflow-frame';

function applyFrame(on) {
  el.shell.classList.toggle('no-frame', !on);
  el.optFrame.checked = on;
  try { localStorage.setItem(FRAME_KEY, on ? '1' : '0'); } catch { /* diabaikan */ }
  morphFit();
  syncMenu();
}

el.optFrame.onchange = () => applyFrame(el.optFrame.checked);

/* Animasi UI: default nyala, bisa dimatikan; tersimpan di peramban.
   (Windows "Show animations" off tidak lagi mematikan animasi di aplikasi ini.) */
const MOTION_KEY = 'mirrorflow-motion';

function applyMotion(on) {
  document.documentElement.dataset.motion = on ? 'on' : 'off';
  el.optMotion.checked = on;
  try { localStorage.setItem(MOTION_KEY, on ? '1' : '0'); } catch { /* diabaikan */ }
  syncMenu();
}

el.optMotion.onchange = () => applyMotion(el.optMotion.checked);

try { applyMotion(localStorage.getItem(MOTION_KEY) !== '0'); }
catch { applyMotion(true); }

/* bagian inspector: buka / tutup dengan animasi (bukan <details> yang menyentak) */
for (const title of document.querySelectorAll('.sect-title')) {
  title.onclick = () => title.closest('.sect').classList.toggle('collapsed');
}

try {
  if (localStorage.getItem(FRAME_KEY) === '0') applyFrame(false);
} catch { /* diabaikan */ }

window.addEventListener('resize', fitCanvas);
window.addEventListener('load', fitCanvas);
window.addEventListener('keydown', (event) => {
  if (event.target?.tagName?.toLowerCase() === 'input') return;
  if (event.key === 'F' || event.key === 'f') { /* f diteruskan ke device */ }
});
document.addEventListener('fullscreenchange', () => {
  const isFullscreen = !!document.fullscreenElement;
  document.body.classList.toggle('fullscreen', isFullscreen);
  if (isFullscreen) {
    document.body.classList.remove('inspector-visible');
  }
  // auto-hide Dynamic Island in fullscreen (lebih clean seperti iPhone asli)
  const island = document.querySelector('.island');
  if (island) island.style.display = isFullscreen ? 'none' : '';
  // juga sembunyikan tombol samping biar clean
  document.querySelectorAll('.side-btn').forEach(btn => btn.style.display = isFullscreen ? 'none' : '');
  fitCanvas();
});

/* Floating inspector toggle di fullscreen */
document.addEventListener('click', (e) => {
  const btn = e.target.closest('#fs-inspector-toggle');
  if (!btn) return;
  document.body.classList.toggle('inspector-visible');
  fitCanvas();
});

function toggleFullscreen() {
  if (document.fullscreenElement) document.exitFullscreen();
  else document.documentElement.requestFullscreen?.();
}

/* ============================================================
   STREAM SOURCE MODE — untuk TikTok Studio / OBS
   Toggle: Alt+S atau tombol di toolbar
   Efek: nyembunyiin semua UI, canvas doang yang tampil
   ============================================================ */
function toggleStreamSource() {
  document.body.classList.toggle('stream-source');
  const isSource = document.body.classList.contains('stream-source');
  toast(isSource ? 'Mode Stream Source AKTIF — buka TikTok Studio, pilih Window Capture' : 'Mode Stream Source NONAKTIF');
  fitCanvas();
}

// Keyboard shortcut: Alt+S
document.addEventListener('keydown', (e) => {
  if (e.altKey && (e.key === 's' || e.key === 'S')) {
    e.preventDefault();
    toggleStreamSource();
  }
});

// Tombol di toolbar
$('btn-stream-source')?.addEventListener('click', toggleStreamSource);

/* ---------------- menu bar ala macOS ---------------- */
const menubar = $('menubar');
const menuRoots = menubar ? [...menubar.querySelectorAll('.menu')] : [];

function closeMenus() {
  for (const menu of menuRoots) menu.classList.remove('open');
}

/* centang untuk item yang punya status nyala/mati, sama seperti menu View Xcode */
function syncMenu() {
  const bar = document.getElementById('menubar');
  if (!bar) return;
  const workarea = document.querySelector('.workarea');
  const cluster = document.querySelector('.cluster');
  const appEl = document.querySelector('.app');
  const flags = {
    nav: !workarea.classList.contains('nav-hidden'),
    insp: !cluster.classList.contains('insp-hidden'),
    console: !appEl.classList.contains('console-hidden'),
    frame: el.optFrame.checked,
    awake: el.optAwake.checked,
    touches: el.optTouches.checked,
    motion: el.optMotion.checked,
  };
  for (const [act, on] of Object.entries(flags)) {
    const node = bar.querySelector(`.menu-item[data-act="${act}"]`);
    if (node) node.classList.toggle('checked', on);
  }
}

/* semuanya diteruskan ke tombol yang sudah ada, jadi tidak ada logika ganda */
const cmdButton = (name) => document.querySelector(`.tool[data-cmd="${name}"]`);

const menuTargets = {
  theme: () => $('btn-theme'),
  shot: () => $('btn-shot'),
  refresh: () => $('btn-refresh'),
  run: () => $('btn-run'),
  stop: () => $('btn-stop'),
  pause: () => $('btn-pause'),
  nav: () => $('btn-nav-toggle'),
  insp: () => $('btn-insp-toggle'),
  console: () => $('btn-console'),
  frame: () => $('opt-frame'),
  motion: () => $('opt-motion'),
  fullscreen: () => $('btn-fullscreen'),
  home: () => cmdButton('home'),
  back: () => cmdButton('back'),
  recents: () => cmdButton('recents'),
  rotate: () => cmdButton('rotate'),
  power: () => cmdButton('power'),
  notifications: () => cmdButton('notifications'),
  settings: () => cmdButton('settings'),
};

/* aksi yang bukan sekadar klik tombol */
function deviceCommand(name, args = {}) {
  if (!state.attached) return toast('Belum ada sesi aktif', true);
  send({ t: 'command', serial: state.attached, name, args });
}

function deviceKey(code) {
  if (!state.attached) return toast('Belum ada sesi aktif', true);
  input.tapKey(code);
}

const menuActions = {
  'menu-key': () => deviceKey(AKEY.MENU),
  'vol-up': () => deviceKey(AKEY.VOLUME_UP),
  'vol-down': () => deviceKey(AKEY.VOLUME_DOWN),
  collapse: () => deviceCommand('collapse'),
  awake: () => $('opt-awake').click(),
  touches: () => $('opt-touches').click(),
  'help-input': () => {
    pushLog('Kontrol lewat mouse: klik = tap · tahan = long press · seret = swipe', 'info');
    pushLog('Roda mouse = scroll · klik dua kali = layar penuh', 'info');
    toast('Detail kontrol ada di konsol');
  },
  'help-keys': () => {
    pushLog('Ctrl+B Back · Ctrl+H Home · Ctrl+S Recent · Ctrl+P Power', 'info');
    pushLog('Ctrl+O kunci layar · Ctrl+M menu · Ctrl+↑/↓ volume', 'info');
    pushLog('Ketik langsung = teks ke device · Esc = tutup menu', 'info');
    toast('Daftar tombol ada di konsol');
  },
  'help-start': () => {
    pushLog('1) Colokkan HP, aktifkan USB debugging, terima dialog di layar HP', 'info');
    pushLog('2) Klik device di navigator kiri, atau tombol ▶ di toolbar', 'info');
    pushLog('3) Belum ada HP? Jalankan: python -m mirrorflow --demo', 'info');
    toast('Langkahnya ditulis di konsol');
  },
  'help-trouble': () => {
    pushLog('Device tidak muncul? Cek: adb devices · izin USB debugging · kabel data', 'info');
    pushLog('Status "unauthorized": cabut-colok lalu terima dialog di HP', 'info');
    toast('Panduan ditulis di konsol');
  },
};

for (const menu of menuRoots) {
  const title = menu.querySelector('.menu-title');
  title.onclick = (event) => {
    event.stopPropagation();
    const wasOpen = menu.classList.contains('open');
    closeMenus();
    if (!wasOpen) menu.classList.add('open');
  };
  // kalau satu menu sudah terbuka, mengarahkan mouse ke judul lain langsung pindah
  title.onmouseenter = () => {
    if (!menuRoots.some((m) => m.classList.contains('open'))) return;
    closeMenus();
    menu.classList.add('open');
  };
}

menuActions.about = () => {
  pushLog(`MirrorFlow ${el.version.textContent} · render WebCodecs · protokol scrcpy v5.0.1`, 'info');
  toast(`MirrorFlow ${el.version.textContent}`);
};

for (const item of menubar.querySelectorAll('.menu-item')) {
  item.onclick = () => {
    const act = item.dataset.act;
    closeMenus();

    const special = menuActions[act];
    if (special) {
      special();
      return syncMenu();
    }

    const target = menuTargets[act]?.();
    if (!target) return toast(`Aksi "${act}" belum tersedia`, true);
    target.click();
    syncMenu();
  };
}

document.addEventListener('click', (event) => {
  if (menubar && !menubar.contains(event.target)) closeMenus();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') closeMenus();
});
syncMenu();

/* ---------------- tema ---------------- */
/* Tema mengikuti sistem; sekali user memilih, pilihannya diingat. */
const THEME_KEY = 'mirrorflow-theme';

function savedTheme() {
  try {
    const value = localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;      // localStorage bisa diblokir
  }
}

function applyTheme(theme) {
  const root = document.documentElement;
  if (theme) root.dataset.theme = theme;
  else delete root.dataset.theme;
}

const initialTheme = savedTheme();
if (initialTheme) applyTheme(initialTheme);

$('btn-theme').onclick = () => {
  const systemDark = window.matchMedia?.('(prefers-color-scheme: dark)').matches;
  const current = document.documentElement.dataset.theme || (systemDark ? 'dark' : 'light');
  const next = current === 'dark' ? 'light' : 'dark';
  applyTheme(next);
  try { localStorage.setItem(THEME_KEY, next); } catch { /* diabaikan */ }
  toast(`Tema ${next === 'dark' ? 'gelap' : 'terang'}`);
};

/* ---------------- start ---------------- */
window.__mf = { state, renderer, input, send, requestAttach, fitCanvas };
connect();
setStatus('Menghubungkan ke server…', 'busy');
