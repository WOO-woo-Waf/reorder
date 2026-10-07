import { afterEach, describe, expect, it, vi } from 'vitest';
import type { EngineClient } from './engine-client';
import type { Method, JobSnapshot, PackageSnapshot, PackageState, SystemInfo } from './contracts';
import { DesktopController, type DesktopState } from './desktop-controller';

type Responder = (params: Record<string, unknown>) => unknown | Promise<unknown>;

class FakeClient implements EngineClient {
  calls: { method: Method; params: Record<string, unknown> }[] = [];
  script: Partial<Record<Method, Responder>> = {};
  async request<T>(method: Method, params: Record<string, unknown> = {}): Promise<T> {
    this.calls.push({ method, params });
    const responder = this.script[method];
    if (responder) return (await responder(params)) as T;
    if (method === 'system.info') return { settings: { options: {} }, tools: {}, passwords: { count: 0, storage: 'session' } } as T;
    if (method === 'jobs.list') return [] as T;
    if (method === 'plans.create') return { plan_id: 'plan', packages: [{ package_id: 'p', name: 'a.zip', members: 1, bytes: 1 }], warnings: [], output_root: '/out' } as T;
    if (method === 'jobs.start') return { job_id: 'job', packages: [], state: 'queued' } as T;
    if (method === 'jobs.retry') return { job_id: 'retry', packages: [], state: 'queued' } as T;
    throw new Error('unexpected method: ' + method);
  }
  async openResult(): Promise<void> {}
  callsOf(method: Method): { method: Method; params: Record<string, unknown> }[] {
    return this.calls.filter(c => c.method === method);
  }
}

const instances: DesktopController[] = [];
afterEach(() => { instances.forEach(x => x.dispose()); instances.length = 0; vi.useRealTimers(); });

function observe(controller: DesktopController): () => DesktopState {
  let latest: DesktopState | undefined;
  controller.subscribe(state => { latest = state; });
  return () => latest as DesktopState;
}
function deferred<T>(): { promise: Promise<T>; resolve: (value: T) => void } {
  let settle!: (value: T) => void;
  const promise = new Promise<T>(resolve => { settle = resolve; });
  return { promise, resolve: settle };
}
function packageRows(count: number, state: PackageState = 'extracting'): PackageSnapshot[] {
  return Array.from({ length: count }, (_, index) => ({ package_id: `p${index}`, name: `file-${index}.zip`, state, message: '', error_code: null, results: [] }));
}
function jobSnapshot(overrides: Partial<JobSnapshot> & { job_id: string }): JobSnapshot {
  return { plan_id: 'plan', state: 'running', created_at: '', updated_at: '', packages: [], last_seq: 0, retry_of: null, ...overrides };
}
const systemInfo: SystemInfo = {
  settings: { version: 1, options: { deep_extract: false, max_depth: 2, min_archive_mb: 100, final_single_mb: 1024, preserve_payload_names: true, recursive: false, tool_timeout_sec: 600, max_output_gb: 50, keep_workspace: false }, tools: { seven_zip: null, unrar: null, bandizip: null } },
  tools: { seven_zip: null, unrar: null, bandizip: null },
  passwords: { count: 0, storage: 'session' }, version: '0.2.0', protocol_version: 1, platform: 'win32', data_root: '/data', capabilities: [],
};

describe('manual processing workflow', () => {
  it('requires a preview and retains the idempotency key on repeated starts', async () => {
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    await controller.initialize();
    await controller.start();
    expect(client.calls.some(c => c.method === 'jobs.start')).toBe(false);
    controller.addInputs(['/in/a.zip']); controller.setOutput('/out');
    await controller.prepare(); await controller.start(); await controller.start();
    const starts = client.calls.filter(c => c.method === 'jobs.start');
    expect(starts).toHaveLength(2);
    expect(starts[0].params.idempotency_key).toBe(starts[1].params.idempotency_key);
  });
  it('retries only failed or interrupted packages, preserving successful and review packages', async () => {
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    controller.selectJob({ job_id: 'old', state: 'partial', packages: [
      { package_id: 'success', state: 'succeeded' }, { package_id: 'failure', state: 'failed' },
      { package_id: 'review', state: 'needs_review' },
    ] } as JobSnapshot);
    await controller.retry();
    const request = client.calls.find(c => c.method === 'jobs.retry');
    expect(request?.params.package_ids).toEqual(['failure']);
  });
});

describe('desktop lifecycle regressions', () => {
  it('re-initializes and returns to ready when refresh runs after a failed startup', async () => {
    vi.useFakeTimers();
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    const state = observe(controller);
    let online = false;
    client.script['system.info'] = () => { if (!online) throw new Error('engine offline'); return systemInfo; };
    await controller.initialize();
    expect(state().ready).toBe(false);
    expect(state().error).toBe('engine offline');
    online = true;
    await controller.loadHistory();
    expect(state().ready).toBe(true);
    expect(state().error).toBe('');
    expect(state().info?.version).toBe('0.2.0');
  });

  it('shows the full active task from jobs.get instead of the truncated jobs.list summary', async () => {
    vi.useFakeTimers();
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    const state = observe(controller);
    let list: JobSnapshot[] = [];
    client.script['jobs.list'] = () => list;
    client.script['jobs.get'] = params => jobSnapshot({ job_id: String(params.job_id), state: 'running', packages: packageRows(7), package_count: 7 });
    await controller.initialize();
    expect(state().job).toBeNull();
    const summary = jobSnapshot({ job_id: 'active', state: 'running', packages: packageRows(5), package_count: 7 });
    list = [summary];
    await controller.loadHistory();
    expect(summary.packages).toHaveLength(5);
    expect(client.callsOf('jobs.get').some(c => c.params.job_id === 'active')).toBe(true);
    expect(state().job?.packages).toHaveLength(7);
    expect(state().job?.package_count).toBe(7);
  });

  it('delivers cancel while another operation is busy without clearing its busy state', async () => {
    vi.useFakeTimers();
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    const state = observe(controller);
    client.script['system.info'] = () => systemInfo;
    client.script['jobs.list'] = () => [];
    client.script['jobs.get'] = params => jobSnapshot({ job_id: String(params.job_id), state: 'running', packages: packageRows(1) });
    client.script['jobs.cancel'] = () => ({ accepted: true });
    const pendingLogs = deferred<{ lines: string[] }>();
    client.script['jobs.logs'] = () => pendingLogs.promise;
    await controller.initialize();
    controller.selectJob(jobSnapshot({ job_id: 'active', state: 'running', packages: packageRows(1) }));
    const inFlight = controller.showLogs();
    expect(state().busy).toBe(true);
    await controller.cancel();
    expect(client.callsOf('jobs.cancel')).toHaveLength(1);
    expect(state().busy).toBe(true);
    expect(state().notice).toBe('已请求取消，正在完成当前安全步骤。');
    pendingLogs.resolve({ lines: [] });
    await inFlight;
    expect(state().busy).toBe(false);
  });

  it('does not restore busy when the other operation finishes before cancel does', async () => {
    vi.useFakeTimers();
    const client = new FakeClient(); const controller = new DesktopController(client); instances.push(controller);
    const state = observe(controller);
    client.script['system.info'] = () => systemInfo;
    client.script['jobs.list'] = () => [];
    client.script['jobs.get'] = params => jobSnapshot({ job_id: String(params.job_id), state: 'running', packages: packageRows(1) });
    const pendingLogs = deferred<{ lines: string[] }>();
    const pendingCancel = deferred<{ accepted: boolean }>();
    client.script['jobs.logs'] = () => pendingLogs.promise;
    client.script['jobs.cancel'] = () => pendingCancel.promise;
    await controller.initialize();
    controller.selectJob(jobSnapshot({ job_id: 'active', state: 'running', packages: packageRows(1) }));
    const inFlight = controller.showLogs();
    const cancelling = controller.cancel();
    expect(state().busy).toBe(true);
    expect(client.callsOf('jobs.cancel')).toHaveLength(1);
    pendingLogs.resolve({ lines: [] });
    await inFlight;
    expect(state().busy).toBe(false);
    pendingCancel.resolve({ accepted: true });
    await cancelling;
    expect(state().busy).toBe(false);
    expect(state().notice).toBe('已请求取消，正在完成当前安全步骤。');
  });
});
