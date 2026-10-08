/**
 * Appearance settings for the desktop shell.
 *
 * This module is deliberately independent from the processing engine: nothing here talks to
 * Rust, IPC or the filesystem. Custom backgrounds are chosen with an HTML file input, read with
 * FileReader inside the webview, validated, and persisted in localStorage only. There is no way
 * to inject CSS or point the background at a remote URL.
 */

export const APPEARANCE_STORAGE_KEY = 'hoshiribbon.appearance.v1';
/** Bundled default art, served from apps/desktop/public at the webview root. */
export const DEFAULT_BACKGROUND_URL = '/backgrounds/hoshiribbon-default.png';
export const MAX_IMAGE_BYTES = 2 * 1024 * 1024;
export const MAX_IMAGE_EDGE = 8192;
export const MAX_IMAGE_PIXELS = 16 * 1024 * 1024;
export const ALLOWED_MIME = ['image/png', 'image/jpeg', 'image/webp'] as const;
export const OVERLAY_MAX = 100;
export const BLUR_MAX = 24;
export const DEFAULT_OVERLAY = 62;
export const DEFAULT_BLUR = 0;

export type BackgroundMode = 'default' | 'custom' | 'none';

export interface AppearanceSettings {
  version: 1;
  mode: BackgroundMode;
  /** Validated `data:image/...;base64,...` payload, or null when using the bundled art. */
  dataUrl: string | null;
  /** Mask strength over the background, 0 (clear) - 100 (opaque). */
  overlay: number;
  /** Background blur in px, 0 - 24. */
  blur: number;
}

export const defaultAppearance: AppearanceSettings = {
  version: 1, mode: 'default', dataUrl: null, overlay: DEFAULT_OVERLAY, blur: DEFAULT_BLUR,
};

/** Minimal localStorage surface so tests can inject a fake and quota/read-back errors are reachable. */
export interface StorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface ImageCandidate { name: string; type: string; size: number }

export type CheckResult = { ok: true } | { ok: false; reason: string };
export type SaveResult = { ok: true } | { ok: false; reason: string };
export type ImageResult =
  | { ok: true; dataUrl: string; width: number; height: number }
  | { ok: false; reason: string };

/** Narrow seam over FileReader so the reader can be mocked without a DOM. */
export interface ReaderLike {
  result: string | ArrayBuffer | null;
  onload: (() => void) | null;
  onerror: (() => void) | null;
  readAsDataURL(file: ImageCandidate): void;
}

export interface ImageSize { width: number; height: number }
export type Decoder = (dataUrl: string) => Promise<ImageSize>;
export interface ImageDeps { createReader?: () => ReaderLike; decode?: Decoder }
export interface StyleTarget { style: { setProperty(name: string, value: string): void } }

export function isAllowedMime(type: string): boolean {
  return typeof type === 'string' && (ALLOWED_MIME as readonly string[]).includes(type);
}

const DATA_URL_PATTERN = /^data:image\/(?:png|jpeg|webp);base64,[A-Za-z0-9+/]+={0,2}$/;
const DATA_URL_MAX_LENGTH = 4 * 1024 * 1024;

/** Only a self-produced raster data URL passes: no remote URL, no SVG, no CSS metacharacters. */
export function isAllowedDataUrl(value: unknown): value is string {
  return typeof value === 'string' && value.length > 32 && value.length <= DATA_URL_MAX_LENGTH && DATA_URL_PATTERN.test(value);
}

export function checkImageFile(file: ImageCandidate | null | undefined): CheckResult {
  if (!file || typeof file.size !== 'number' || typeof file.type !== 'string') return { ok: false, reason: '无法读取该文件。' };
  if (!isAllowedMime(file.type)) return { ok: false, reason: '只支持 PNG、JPEG、WebP 图片，不支持 SVG 或在线图片。' };
  if (file.size <= 0) return { ok: false, reason: '图片内容为空。' };
  if (file.size > MAX_IMAGE_BYTES) return { ok: false, reason: `图片超过 ${Math.round(MAX_IMAGE_BYTES / 1024 / 1024)} MiB 上限。` };
  return { ok: true };
}

export function clampNumber(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = typeof value === 'number' ? value : Number(value);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, Math.round(parsed)));
}
export function clampOverlay(value: unknown): number { return clampNumber(value, 0, OVERLAY_MAX, DEFAULT_OVERLAY); }
export function clampBlur(value: unknown): number { return clampNumber(value, 0, BLUR_MAX, DEFAULT_BLUR); }

/** Coerce anything into a valid settings object; unusable custom images fall back to the bundled art. */
export function normalizeAppearance(settings: Partial<AppearanceSettings> | null | undefined): AppearanceSettings {
  const source = settings && typeof settings === 'object' ? settings : {};
  const mode: BackgroundMode = source.mode === 'custom' || source.mode === 'none' ? source.mode : 'default';
  const dataUrl = isAllowedDataUrl(source.dataUrl) ? source.dataUrl : null;
  const resolved: BackgroundMode = mode === 'custom' && !dataUrl ? 'default' : mode;
  return {
    version: 1,
    mode: resolved,
    dataUrl: resolved === 'custom' ? dataUrl : null,
    overlay: clampOverlay(source.overlay),
    blur: clampBlur(source.blur),
  };
}

export function parseAppearance(raw: string | null | undefined): AppearanceSettings {
  if (typeof raw !== 'string' || !raw) return { ...defaultAppearance };
  let parsed: unknown;
  try { parsed = JSON.parse(raw); } catch { return { ...defaultAppearance }; }
  if (!parsed || typeof parsed !== 'object') return { ...defaultAppearance };
  return normalizeAppearance(parsed as Partial<AppearanceSettings>);
}

export function serializeAppearance(settings: AppearanceSettings): string {
  return JSON.stringify(normalizeAppearance(settings));
}

export function loadAppearance(storage: StorageLike | null | undefined): AppearanceSettings {
  if (!storage) return { ...defaultAppearance };
  try { return parseAppearance(storage.getItem(APPEARANCE_STORAGE_KEY)); }
  catch { return { ...defaultAppearance }; }
}

/** Writes, then reads back to confirm the value landed. A failed write never looks like success. */
export function saveAppearance(storage: StorageLike | null | undefined, settings: AppearanceSettings): SaveResult {
  if (!storage) return { ok: false, reason: '当前环境不支持本地存储，未保存外观，已保留原设置。' };
  const payload = serializeAppearance(settings);
  try { storage.setItem(APPEARANCE_STORAGE_KEY, payload); }
  catch { return { ok: false, reason: '本地存储写入失败（可能空间不足），已保留原外观设置。' }; }
  let stored: string | null;
  try { stored = storage.getItem(APPEARANCE_STORAGE_KEY); }
  catch { return { ok: false, reason: '本地存储读取失败，已保留原外观设置。' }; }
  if (stored !== payload) return { ok: false, reason: '外观设置未能写入本地存储，已保留原外观设置。' };
  return { ok: true };
}

function defaultReader(): ReaderLike { return new FileReader() as unknown as ReaderLike; }

function boundedSize(size: ImageSize): boolean {
  return Number.isInteger(size.width) && Number.isInteger(size.height) && size.width > 0 && size.height > 0
    && size.width <= MAX_IMAGE_EDGE && size.height <= MAX_IMAGE_EDGE
    && size.width * size.height <= MAX_IMAGE_PIXELS;
}

/** Read container dimensions before asking the browser to decode pixels. */
export function rasterHeaderSize(dataUrl: string): ImageSize | null {
  try {
    const bytes = Uint8Array.from(atob(dataUrl.slice(dataUrl.indexOf(',') + 1)), c => c.charCodeAt(0));
    const view = new DataView(bytes.buffer);
    const text = (offset: number, length: number) => String.fromCharCode(...bytes.slice(offset, offset + length));
    if (dataUrl.startsWith('data:image/png;') && bytes.length >= 24
      && text(0, 8) === '\x89PNG\r\n\x1a\n' && text(12, 4) === 'IHDR') {
      return { width: view.getUint32(16), height: view.getUint32(20) };
    }
    if (dataUrl.startsWith('data:image/jpeg;') && bytes[0] === 255 && bytes[1] === 216) {
      let offset = 2;
      while (offset + 4 <= bytes.length) {
        if (bytes[offset++] !== 255) return null;
        while (bytes[offset] === 255) offset++;
        const marker = bytes[offset++];
        if (marker === 217 || marker === 218) return null;
        if (marker === 1 || (marker >= 208 && marker <= 215)) continue;
        const length = view.getUint16(offset);
        if (length < 2 || offset + length > bytes.length) return null;
        if ([192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207].includes(marker) && length >= 7) {
          return { width: view.getUint16(offset + 5), height: view.getUint16(offset + 3) };
        }
        offset += length;
      }
    }
    if (dataUrl.startsWith('data:image/webp;') && text(0, 4) === 'RIFF' && text(8, 4) === 'WEBP') {
      let offset = 12;
      while (offset + 8 <= bytes.length) {
        const kind = text(offset, 4), length = view.getUint32(offset + 4, true), start = offset + 8;
        if (start + length > bytes.length) return null;
        const uint24 = (at: number) => bytes[at] | (bytes[at + 1] << 8) | (bytes[at + 2] << 16);
        if (kind === 'VP8X' && length >= 10) return { width: uint24(start + 4) + 1, height: uint24(start + 7) + 1 };
        if (kind === 'VP8L' && length >= 5 && bytes[start] === 47) return {
          width: 1 + (bytes[start + 1] | ((bytes[start + 2] & 63) << 8)),
          height: 1 + ((bytes[start + 2] >> 6) | (bytes[start + 3] << 2) | ((bytes[start + 4] & 15) << 10)),
        };
        if (kind === 'VP8 ' && length >= 10 && text(start + 3, 3) === '\x9d\x01\x2a') return {
          width: view.getUint16(start + 6, true) & 16383, height: view.getUint16(start + 8, true) & 16383,
        };
        offset = start + length + (length % 2);
      }
    }
  } catch { /* truncated or invalid container */ }
  return null;
}

function defaultDecode(dataUrl: string): Promise<ImageSize> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => resolve({ width: image.naturalWidth, height: image.naturalHeight });
    image.onerror = () => reject(new Error('decode-error'));
    image.src = dataUrl;
  });
}

function readAsDataUrl(reader: ReaderLike, file: ImageCandidate): Promise<string> {
  return new Promise((resolve, reject) => {
    reader.onload = () => {
      if (typeof reader.result === 'string' && reader.result) resolve(reader.result);
      else reject(new Error('empty-result'));
    };
    reader.onerror = () => reject(new Error('read-error'));
    reader.readAsDataURL(file);
  });
}

/**
 * Reads one local image end to end: MIME and size guard, FileReader, data URL shape, real decode
 * and positive dimensions. Every failure returns a reason and never yields a usable data URL.
 */
export async function readLocalImage(file: ImageCandidate, deps: ImageDeps = {}): Promise<ImageResult> {
  const check = checkImageFile(file);
  if (!check.ok) return check;
  let dataUrl: string;
  try { dataUrl = await readAsDataUrl((deps.createReader ?? defaultReader)(), file); }
  catch { return { ok: false, reason: '读取图片失败，请重试。' }; }
  if (!isAllowedDataUrl(dataUrl) || !dataUrl.startsWith(`data:${file.type};base64,`)) {
    return { ok: false, reason: '图片数据无效，未使用该文件。' };
  }
  const header = rasterHeaderSize(dataUrl);
  if (!header) return { ok: false, reason: '图片文件头无效或格式不符。' };
  if (!boundedSize(header)) return { ok: false, reason: '图片尺寸过大，请使用边长不超过 8192、总像素不超过 16 Mi 的图片。' };
  let size: ImageSize;
  try { size = await (deps.decode ?? defaultDecode)(dataUrl); }
  catch { return { ok: false, reason: '无法解码该图片，可能已损坏或格式不符。' }; }
  if (!size || !boundedSize(size)) {
    return { ok: false, reason: '图片尺寸无效，未使用该文件。' };
  }
  return { ok: true, dataUrl, width: Math.round(size.width), height: Math.round(size.height) };
}

/** The one place a background URL becomes CSS: only the bundled constant or a validated data URL. */
export function backgroundLayer(settings: AppearanceSettings): string {
  const normalized = normalizeAppearance(settings);
  if (normalized.mode === 'none') return '';
  if (normalized.mode === 'custom' && normalized.dataUrl) return `url("${normalized.dataUrl}")`;
  return `url("${DEFAULT_BACKGROUND_URL}")`;
}

export function appearanceVars(settings: AppearanceSettings): Record<string, string> {
  const normalized = normalizeAppearance(settings);
  const layer = backgroundLayer(normalized);
  return {
    '--hr-bg-image': layer || 'none',
    '--hr-overlay': String(layer ? normalized.overlay / 100 : 0),
    '--hr-blur': `${layer ? normalized.blur : 0}px`,
  };
}

export function applyAppearance(target: StyleTarget | null | undefined, settings: AppearanceSettings): void {
  if (!target) return;
  for (const [name, value] of Object.entries(appearanceVars(settings))) target.style.setProperty(name, value);
}
