/**
 * Parser framing scrcpy + decoder WebCodecs + render ke canvas.
 *
 * Format di socket video (big-endian, dari scrcpy v5.0.1 Streamer.java):
 *   [codec id u32]                                  0x68323634 = "h264"
 *   lalu berulang:
 *     session packet: byte0 MSB = 1  -> flags u32, width u32, height u32
 *     media packet:   byte0 MSB = 0  -> ptsAndFlags u64, size u32, payload[size]
 *       bit62 = config packet (SPS/PPS)
 *       bit61 = key frame
 *
 * scrcpy mengirim H.264 dalam format Annex-B, jadi decoder WebCodecs
 * dikonfigurasi TANPA `description` (Chrome memperlakukannya sebagai Annex-B).
 */

const CODEC_ID_H264 = 0x68323634;
const CODEC_ID_H265 = 0x68323635;

const CODEC_NAMES = {
  [CODEC_ID_H264]: 'h264',
  [CODEC_ID_H265]: 'h265',
};

const MASK_61 = (1n << 61n) - 1n;

export class StreamRenderer {
  /**
   * @param {HTMLCanvasElement} canvas
   * @param {{onResolution?: Function, onStats?: Function, onCodec?: Function, onError?: Function}} hooks
   */
  constructor(canvas, hooks = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d', { alpha: false, desynchronized: true });
    this.hooks = hooks;

    this.codec = null;          // 'h264' | 'h265'
    this.codecString = null;    // 'avc1.64001f'
    this.width = 0;
    this.height = 0;
    this.decoder = null;

    // SPS/PPS terakhir. Dipakai ulang kalau decoder harus dibangun ulang,
    // supaya tidak pernah men-decoder frame tanpa parameter set.
    this.configPayload = null;
    this.needKey = true;        // buang frame sampai keyframe pertama
    this._pendingConfig = false;

    this.buffer = new Uint8Array(1 << 16);
    this.length = 0;

    this.frames = 0;
    this.bytes = 0;
    this.dropped = 0;
    this._fpsWindow = { t: performance.now(), n: 0 };
  }

  /* ---------------- buffer ---------------- */

  push(chunk) {
    this.bytes += chunk.byteLength;
    const need = this.length + chunk.byteLength;
    if (need > this.buffer.length) {
      let size = this.buffer.length;
      while (size < need) size *= 2;
      const grown = new Uint8Array(size);
      grown.set(this.buffer.subarray(0, this.length));
      this.buffer = grown;
    }
    this.buffer.set(chunk, this.length);
    this.length = need;
    this._parse();
  }

  _compact(offset) {
    if (offset === 0) return;
    this.buffer.copyWithin(0, offset, this.length);
    this.length -= offset;
  }

  _parse() {
    let o = 0;
    const b = this.buffer;

    while (this.length - o >= 4) {
      if (this.codec === null) {
        const id = (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3];
        const name = CODEC_NAMES[id >>> 0];
        o += 4;
        if (!name) {
          this.hooks.onError?.(`codec tidak dikenal: 0x${(id >>> 0).toString(16)}`);
          this.codec = null;
          break;
        }
        this.codec = name;
        this.hooks.onCodec?.(name);
        continue;
      }

      if (this.length - o < 12) break;
      const head = b[o];

      // session packet
      if (head & 0x80) {
        const width = (b[o + 4] << 24) | (b[o + 5] << 16) | (b[o + 6] << 8) | b[o + 7];
        const height = (b[o + 8] << 24) | (b[o + 9] << 16) | (b[o + 10] << 8) | b[o + 11];
        o += 12;
        this._onSession(width >>> 0, height >>> 0);
        continue;
      }

      // media packet
      const size = (b[o + 8] << 24) | (b[o + 9] << 16) | (b[o + 10] << 8) | b[o + 11];
      const isConfig = (head & 0x40) !== 0;
      const isKey = (head & 0x20) !== 0;

      if (size < 0 || this.length - o - 12 < size) break;

      const payload = b.subarray(o + 12, o + 12 + size);
      const ptsHi = (b[o] << 24) | (b[o + 1] << 16) | (b[o + 2] << 8) | b[o + 3];
      const ptsLo = (b[o + 4] << 24) | (b[o + 5] << 16) | (b[o + 6] << 8) | b[o + 7];
      const pts = Number((((BigInt(ptsHi >>> 0) << 32n) | BigInt(ptsLo >>> 0)) & MASK_61));

      // payload adalah view ke buffer; salin karena buffer bisa dipakai ulang
      const copy = payload.slice();
      o += 12 + size;

      this._onPacket(copy, isConfig, isKey, pts);
    }

    this._compact(o);
  }

  /* ---------------- decoder ---------------- */

  _onSession(width, height) {
    if (width === this.width && height === this.height) return;
    this.width = width;
    this.height = height;
    if (width > 0 && height > 0) {
      this.canvas.width = width;
      this.canvas.height = height;
      this.hooks.onResolution?.(width, height);
    }
  }

  _ensureDecoder() {
    if (this.decoder) return true;
    if (!this.codecString || typeof VideoDecoder === 'undefined') return false;
    try {
      this.decoder = new VideoDecoder({
        output: (frame) => this._onFrame(frame),
        error: (err) => {
          // Decoder WebCodecs mati permanen setelah error. Bangun ulang dan
          // tunggu keyframe berikutnya; ini yang bikin stream tidak "beku".
          this.hooks.onError?.(`decoder: ${err.message ?? err}`);
          this._teardownDecoder();
          this.needKey = true;
          this._pendingConfig = true;
        },
      });
      this.decoder.configure({
        codec: this.codecString,
        optimizeForLatency: true,
      });
      return true;
    } catch (err) {
      this.hooks.onError?.(`configure gagal (${this.codecString}): ${err.message ?? err}`);
      this._teardownDecoder();
      return false;
    }
  }

  _teardownDecoder() {
    if (this.decoder) {
      try { this.decoder.close(); } catch { /* diabaikan */ }
      this.decoder = null;
    }
  }

  _onPacket(payload, isConfig, isKey, pts) {
    if (this.codec !== 'h264') {
      if (this.codec) this.hooks.onError?.(`codec ${this.codec} belum didukung`);
      this.codec = null;
      return;
    }

    if (isConfig) {
      // SPS/PPS disimpan, TAPI tidak di-decode sendirian: setelah configure()
      // WebCodecs menuntut keyframe, dan chunk berisi SPS/PPS saja bukan keyframe
      // (Chrome langsung membalas "Decoding error"). Parameter set nanti
      // dititipkan di depan keyframe berikutnya.
      this.configPayload = payload.slice();
      const detected = parseAvcCodecString(payload);
      if (detected && detected !== this.codecString) {
        this.codecString = detected;
        this._teardownDecoder();
      } else if (!this.codecString) {
        this.codecString = detected || 'avc1.42E01E';
      }
      this.needKey = true;
      return;
    }

    if (!this._ensureDecoder()) return;

    // Belum ada referensi: buang sampai keyframe pertama.
    if (this.needKey && !isKey) {
      this.dropped += 1;
      return;
    }

    // kalau decoder ketinggalan, buang frame non-key supaya latensi tidak menumpuk
    if (this.decoder.decodeQueueSize > 12 && !isKey) {
      this.dropped += 1;
      return;
    }

    if (isKey) {
      this.needKey = false;
      // Selalu titipkan SPS/PPS di depan keyframe: aman kalau decoder baru saja
      // dibangun ulang, dan tidak merusak apa pun kalau tidak.
      if (this.configPayload) {
        this._pendingConfig = false;
        const cfg = this.configPayload;
        const merged = new Uint8Array(cfg.byteLength + payload.byteLength);
        merged.set(new Uint8Array(cfg), 0);
        merged.set(new Uint8Array(payload), cfg.byteLength);
        this._decode(merged, true, pts);
        return;
      }
    }
    this._decode(payload, isKey, pts);
  }

  _decode(payload, isKey, pts) {
    if (!this.decoder || this.decoder.state !== 'configured') return;
    try {
      this.decoder.decode(new EncodedVideoChunk({
        type: isKey ? 'key' : 'delta',
        timestamp: pts || performance.now() * 1000,
        data: payload,
      }));
    } catch (err) {
      this.hooks.onError?.(`decode: ${err.message ?? err}`);
    }
  }

  _onFrame(frame) {
    const { ctx, canvas } = this;
    if (canvas.width === 0 || canvas.height === 0) {
      canvas.width = frame.displayWidth;
      canvas.height = frame.displayHeight;
      this.hooks.onResolution?.(canvas.width, canvas.height);
    }
    try {
      ctx.drawImage(frame, 0, 0, canvas.width, canvas.height);
    } catch { /* frame bisa sudah ditutup */ }
    frame.close();

    this.frames += 1;
    this._fpsWindow.n += 1;
    const now = performance.now();
    if (now - this._fpsWindow.t >= 1000) {
      this.hooks.onStats?.({
        fps: Math.round((this._fpsWindow.n * 1000) / (now - this._fpsWindow.t)),
        mbps: Number(((this.bytes * 8) / 1e6).toFixed(2)),
        dropped: this.dropped,
        frames: this.frames,
      });
      this._fpsWindow = { t: now, n: 0 };
      this.bytes = 0;
    }
  }

  reset() {
    this._teardownDecoder();
    this.codec = null;
    this.codecString = null;
    this.configPayload = null;
    this.needKey = true;
    this._pendingConfig = false;
    this.width = 0;
    this.height = 0;
    this.length = 0;
    this.dropped = 0;
    this.frames = 0;
    this.bytes = 0;
  }

  close() {
    this.reset();
  }
}

/* Cari NAL SPS (tipe 7) di dalam payload Annex-B lalu susun string codec avc1. */
export function parseAvcCodecString(payload) {
  const nals = iterateNals(payload);
  for (const [start, end] of nals) {
    const type = payload[start] & 0x1f;
    if (type !== 7 || end - start < 4) continue;
    const profile = payload[start + 1];
    const constraints = payload[start + 2];
    const level = payload[start + 3];
    const hex = (v) => v.toString(16).padStart(2, '0');
    return `avc1.${hex(profile)}${hex(constraints)}${hex(level)}`;
  }
  return null;
}

/* Iterasi NAL unit Annex-B: hasilkan [awal, akhir) tanpa start code. */
function* iterateNals(data) {
  const starts = [];
  for (let i = 0; i + 2 < data.length; i++) {
    if (data[i] === 0 && data[i + 1] === 0 && data[i + 2] === 1) {
      starts.push(i === 0 || data[i - 1] !== 0 ? i : i - 1);
      i += 2;
    }
  }
  for (let k = 0; k < starts.length; k++) {
    const begin = starts[k] + (data[starts[k]] === 0 ? 4 : 3);
    const stop = k + 1 < starts.length ? starts[k + 1] : data.length;
    if (stop > begin) yield [begin, stop];
  }
}
