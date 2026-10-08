import { describe, expect, it } from 'vitest';
import {
  ALLOWED_MIME, APPEARANCE_STORAGE_KEY, MAX_IMAGE_BYTES,
  applyAppearance, appearanceVars, backgroundLayer, checkImageFile, clampBlur, clampOverlay,
  defaultAppearance, isAllowedDataUrl, isAllowedMime, loadAppearance, normalizeAppearance,
  parseAppearance, readLocalImage, saveAppearance, serializeAppearance, rasterHeaderSize,
  type ImageCandidate, type ReaderLike, type StorageLike,
} from './appearance';

const PNG_BODY = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+afoQAAAAASUVORK5CYII=';
const PNG_URL = `data:image/png;base64,${PNG_BODY}`;
const JPEG_URL = `data:image/jpeg;base64,${PNG_BODY}`;
const pngFile: ImageCandidate = { name: 'art.png', type: 'image/png', size: 4096 };

class MemoryStorage implements StorageLike {
  private readonly map = new Map<string, string>();
  getItem(key: string): string | null { return this.map.has(key) ? this.map.get(key)! : null; }
  setItem(key: string, value: string): void { this.map.set(key, value); }
}
class QuotaStorage implements StorageLike {
  getItem(): string | null { return null; }
  setItem(): void { throw new Error('QuotaExceededError'); }
}
class DroppingStorage implements StorageLike {
  getItem(): string | null { return null; }
  setItem(): void { /* accepts then loses the value, like a full store that lies */ }
}
class ThrowingReadStorage implements StorageLike {
  getItem(): string | null { throw new Error('SecurityError'); }
  setItem(): void { /* unused */ }
}

function readerWith(result: string | ArrayBuffer | null, mode: 'load' | 'error' = 'load'): ReaderLike {
  const reader: ReaderLike = {
    result: null,
    onload: null,
    onerror: null,
    readAsDataURL() {
      reader.result = result;
      queueMicrotask(() => { (mode === 'error' ? reader.onerror : reader.onload)?.(); });
    },
  };
  return reader;
}

describe('image file guard', () => {
  it('accepts the PNG/JPEG/WebP subset within the size cap', () => {
    for (const type of ALLOWED_MIME) expect(checkImageFile({ name: 'x', type, size: 1024 })).toEqual({ ok: true });
    expect(isAllowedMime('image/png')).toBe(true);
    expect(isAllowedMime('image/svg+xml')).toBe(false);
    expect(isAllowedMime('image/gif')).toBe(false);
  });

  it('rejects missing, empty, oversized and non-subset files', () => {
    expect(checkImageFile(undefined).ok).toBe(false);
    expect(checkImageFile({ name: 'a.svg', type: 'image/svg+xml', size: 1024 }).ok).toBe(false);
    expect(checkImageFile({ name: 'a.png', type: 'image/png', size: 0 }).ok).toBe(false);
    expect(checkImageFile({ name: 'a.png', type: 'image/png', size: MAX_IMAGE_BYTES + 1 }).ok).toBe(false);
    expect(checkImageFile({ name: 'a.png', type: 'image/png', size: MAX_IMAGE_BYTES }).ok).toBe(true);
  });
});

describe('data URL guard', () => {
  it('accepts our own raster data URLs', () => {
    expect(isAllowedDataUrl(PNG_URL)).toBe(true);
  });

  it('rejects remote URLs, SVG and CSS injection attempts', () => {
    expect(isAllowedDataUrl('https://evil.example/x.png')).toBe(false);
    expect(isAllowedDataUrl(`data:image/svg+xml;base64,${PNG_BODY}`)).toBe(false);
    expect(isAllowedDataUrl('/backgrounds/hoshiribbon-default.png')).toBe(false);
    expect(isAllowedDataUrl(`data:image/png;base64,${PNG_BODY}");background:url(http://evil)`)).toBe(false);
    expect(isAllowedDataUrl(42)).toBe(false);
  });
});

describe('clamping and normalization', () => {
  it('clamps overlay and blur with sensible fallbacks', () => {
    expect(clampOverlay(999)).toBe(100);
    expect(clampOverlay(-5)).toBe(0);
    expect(clampOverlay('42')).toBe(42);
    expect(clampOverlay('nope')).toBe(defaultAppearance.overlay);
    expect(clampBlur(999)).toBe(24);
    expect(clampBlur(undefined)).toBe(defaultAppearance.blur);
  });

  it('drops a custom mode that has no usable image', () => {
    const normalized = normalizeAppearance({ mode: 'custom', dataUrl: 'https://evil.example/x.png', overlay: 10, blur: 3 });
    expect(normalized.mode).toBe('default');
    expect(normalized.dataUrl).toBeNull();
    expect(normalized.overlay).toBe(10);
    expect(normalized.blur).toBe(3);
  });

  it('keeps the bundled art mode clean of any data URL', () => {
    const normalized = normalizeAppearance({ mode: 'default', dataUrl: PNG_URL, overlay: 100, blur: 999 });
    expect(normalized).toEqual({ version: 1, mode: 'default', dataUrl: null, overlay: 100, blur: 24 });
  });

  it('treats corrupt or hostile stored JSON as the default', () => {
    expect(parseAppearance(null)).toEqual(defaultAppearance);
    expect(parseAppearance('not json')).toEqual(defaultAppearance);
    expect(parseAppearance('"a string"')).toEqual(defaultAppearance);
    expect(parseAppearance(JSON.stringify({ mode: 'custom', dataUrl: 'javascript:alert(1)' })).mode).toBe('default');
  });
});

describe('storage round trip', () => {
  it('saves, verifies and reloads settings', () => {
    const storage = new MemoryStorage();
    const settings = normalizeAppearance({ mode: 'custom', dataUrl: PNG_URL, overlay: 50, blur: 8 });
    expect(saveAppearance(storage, settings)).toEqual({ ok: true });
    expect(storage.getItem(APPEARANCE_STORAGE_KEY)).toBe(serializeAppearance(settings));
    expect(loadAppearance(storage)).toEqual(settings);
  });

  it('reports a quota failure instead of faking a save', () => {
    const result = saveAppearance(new QuotaStorage(), defaultAppearance);
    expect(result.ok).toBe(false);
  });

  it('reports a read-back mismatch as a failure', () => {
    const result = saveAppearance(new DroppingStorage(), defaultAppearance);
    expect(result.ok).toBe(false);
  });

  it('falls back to the default when storage is unavailable', () => {
    expect(loadAppearance(null)).toEqual(defaultAppearance);
    expect(loadAppearance(new ThrowingReadStorage())).toEqual(defaultAppearance);
    expect(saveAppearance(null, defaultAppearance).ok).toBe(false);
  });
});

describe('readLocalImage', () => {
  it('rejects oversized PNG headers before browser decoding', async () => {
    const bytes = Uint8Array.from(atob(PNG_BODY), c => c.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    view.setUint32(16, 20000); view.setUint32(20, 20000);
    const huge = `data:image/png;base64,${btoa(String.fromCharCode(...bytes))}`;
    let decoded = false;
    const result = await readLocalImage(pngFile, { createReader: () => readerWith(huge), decode: async () => { decoded = true; return { width: 20000, height: 20000 }; } });
    expect(result.ok).toBe(false);
    expect(decoded).toBe(false);
  });

  it('reads JPEG and the three WebP container headers without decoding pixels', () => {
    const url = (type: string, bytes: Uint8Array) => `data:image/${type};base64,${btoa(String.fromCharCode(...bytes))}`;
    const jpeg = Uint8Array.from([255,216,255,192,0,8,8,1,224,2,128,0]);
    expect(rasterHeaderSize(url('jpeg', jpeg))).toEqual({ width: 640, height: 480 });
    const webp = (kind: string, data: number[]) => {
      const bytes = new Uint8Array(20 + data.length + data.length % 2);
      bytes.set([...('RIFF').split('').map(c => c.charCodeAt(0))], 0);
      bytes.set([...('WEBP' + kind).split('').map(c => c.charCodeAt(0))], 8);
      new DataView(bytes.buffer).setUint32(4, bytes.length - 8, true);
      new DataView(bytes.buffer).setUint32(16, data.length, true);
      bytes.set(data, 20);
      return url('webp', bytes);
    };
    expect(rasterHeaderSize(webp('VP8X', [0,0,0,0,127,2,0,223,1,0]))).toEqual({ width: 640, height: 480 });
    expect(rasterHeaderSize(webp('VP8L', [47,1,128,0,0]))).toEqual({ width: 2, height: 3 });
    expect(rasterHeaderSize(webp('VP8 ', [0,0,0,157,1,42,128,2,224,1]))).toEqual({ width: 640, height: 480 });
    expect(rasterHeaderSize('data:image/png;base64,AAAA')).toBeNull();
  });

  it('rejects excessive decoded pixels even with a bounded header', async () => {
    const result = await readLocalImage(pngFile, { createReader: () => readerWith(PNG_URL), decode: async () => ({ width: 5000, height: 5000 }) });
    expect(result.ok).toBe(false);
  });

  it('returns decode dimensions for a valid file', async () => {
    const result = await readLocalImage(pngFile, { createReader: () => readerWith(PNG_URL), decode: async () => ({ width: 1920, height: 1080 }) });
    expect(result).toEqual({ ok: true, dataUrl: PNG_URL, width: 1920, height: 1080 });
  });

  it('rejects before reading when the file fails the guard', async () => {
    const result = await readLocalImage({ name: 'x.svg', type: 'image/svg+xml', size: 10 });
    expect(result.ok).toBe(false);
  });

  it('rejects when the reader errors', async () => {
    const result = await readLocalImage(pngFile, { createReader: () => readerWith(null, 'error'), decode: async () => ({ width: 1, height: 1 }) });
    expect(result.ok).toBe(false);
  });

  it('rejects when the data URL type does not match the file', async () => {
    const result = await readLocalImage(pngFile, { createReader: () => readerWith(JPEG_URL), decode: async () => ({ width: 1, height: 1 }) });
    expect(result.ok).toBe(false);
  });

  it('rejects when decoding fails or yields no pixels', async () => {
    const broken = await readLocalImage(pngFile, { createReader: () => readerWith(PNG_URL), decode: async () => { throw new Error('bad'); } });
    expect(broken.ok).toBe(false);
    const empty = await readLocalImage(pngFile, { createReader: () => readerWith(PNG_URL), decode: async () => ({ width: 0, height: 0 }) });
    expect(empty.ok).toBe(false);
  });
});

describe('CSS layer', () => {
  it('maps each mode to a safe background layer', () => {
    expect(backgroundLayer(defaultAppearance)).toBe('url("/backgrounds/hoshiribbon-default.png")');
    expect(backgroundLayer({ ...defaultAppearance, mode: 'none' })).toBe('');
    expect(backgroundLayer({ ...defaultAppearance, mode: 'custom', dataUrl: PNG_URL })).toBe(`url("${PNG_URL}")`);
  });

  it('zeroes overlay and blur when there is no background', () => {
    expect(appearanceVars({ ...defaultAppearance, mode: 'none', overlay: 80, blur: 12 })).toEqual({ '--hr-bg-image': 'none', '--hr-overlay': '0', '--hr-blur': '0px' });
  });

  it('exposes overlay as a fraction and applies to a style target', () => {
    const vars = appearanceVars({ ...defaultAppearance, mode: 'custom', dataUrl: PNG_URL, overlay: 50, blur: 6 });
    expect(vars['--hr-bg-image']).toBe(`url("${PNG_URL}")`);
    expect(vars['--hr-overlay']).toBe('0.5');
    expect(vars['--hr-blur']).toBe('6px');
    const props = new Map<string, string>();
    const target = { style: { setProperty: (name: string, value: string) => { props.set(name, value); } } };
    applyAppearance(target, { ...defaultAppearance, overlay: 50 });
    expect(props.get('--hr-overlay')).toBe('0.5');
    expect(props.get('--hr-bg-image')).toBe('url("/backgrounds/hoshiribbon-default.png")');
    applyAppearance(null, defaultAppearance);
  });
});
