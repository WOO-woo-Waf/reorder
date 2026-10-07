import { invoke, isTauri } from '@tauri-apps/api/core';
import type { Method } from './contracts';

/** Transport seam: a future browser adapter can implement this same interface. */
export interface EngineClient {
  request<T>(method: Method, params?: Record<string, unknown>): Promise<T>;
  openResult(jobId: string, path?: string): Promise<void>;
}

export class TauriEngineClient implements EngineClient {
  async request<T>(method: Method, params: Record<string, unknown> = {}): Promise<T> {
    if (!isTauri()) throw new Error('请通过桌面软件运行。普通浏览器预览没有本地文件处理能力。');
    return invoke<T>('engine_request', { method, params });
  }
  async openResult(jobId: string, path?: string): Promise<void> {
    return invoke('open_result', { jobId, path: path ?? null });
  }
}
