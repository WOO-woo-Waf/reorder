import type { EngineClient } from './engine-client';
import { terminalJobs, retryable } from './contracts';
import type { DesktopSettings, JobSnapshot, ProcessingPlan, SettingsInfo, SystemInfo } from './contracts';

export interface DesktopState {
  ready: boolean; busy: boolean; error: string; notice: string;
  inputs: string[]; outputRoot: string; settings: SettingsInfo | null;
  info: SystemInfo | null; plan: ProcessingPlan | null; job: JobSnapshot | null;
  history: JobSnapshot[]; logs: string[];
}

/** Owns screen workflows, never parses tool logs into task state. */
export class DesktopController {
  private state: DesktopState = {
    ready: false, busy: false, error: '', notice: '', inputs: [], outputRoot: '',
    settings: null, info: null, plan: null, job: null, history: [], logs: [],
  };
  private listeners = new Set<(state: DesktopState) => void>();
  private polling = false;
  private initializing = false;
  private cancelling = false;
  private timer: ReturnType<typeof setInterval> | undefined;
  private startKey: string | null = null;

  constructor(private readonly client: EngineClient) {}

  subscribe(listener: (state: DesktopState) => void): () => void {
    this.listeners.add(listener); listener(this.state);
    return () => { this.listeners.delete(listener); };
  }
  private update(values: Partial<DesktopState>): void {
    this.state = { ...this.state, ...values };
    for (const listener of this.listeners) listener(this.state);
  }
  private async operation(action: () => Promise<void>): Promise<void> {
    if (this.state.busy) { this.update({ notice: '当前操作尚未完成，请稍后再试。' }); return; }
    this.update({ busy: true, error: '', notice: '' });
    try { await action(); }
    catch (error) { this.update({ error: error instanceof Error ? error.message : String(error) }); }
    finally { this.update({ busy: false }); }
  }
  get running(): boolean { return !!this.state.job && !terminalJobs.has(this.state.job.state); }

  /** Safe to call again: a failed startup leaves ready=false, so refresh/loadHistory retry it. */
  async initialize(): Promise<void> {
    if (this.initializing) return;
    this.initializing = true;
    try {
      await this.operation(async () => {
        const [info, history] = await Promise.all([
          this.client.request<SystemInfo>('system.info'),
          this.client.request<JobSnapshot[]>('jobs.list', { limit: 20 }),
        ]);
        const job = history[0] ? await this.client.request<JobSnapshot>('jobs.get', { job_id: history[0].job_id }) : null;
        this.update({ ready: true, info, settings: info, history, job });
      });
    } finally { this.initializing = false; }
    if (!this.timer) this.timer = setInterval(() => { void this.refresh(); }, 1000);
  }
  dispose(): void { if (this.timer) clearInterval(this.timer); this.listeners.clear(); }
  addInputs(paths: string[]): void {
    if (this.running) { this.update({ notice: '任务正在处理，请结束或取消后再添加文件。' }); return; }
    this.startKey = null;
    this.update({ inputs: [...new Set([...this.state.inputs, ...paths])], plan: null, error: '' });
  }
  removeInput(path: string): void {
    if (this.running) return;
    this.startKey = null;
    this.update({ inputs: this.state.inputs.filter(p => p !== path), plan: null });
  }
  setOutput(path: string): void {
    if (this.running) return;
    this.startKey = null;
    this.update({ outputRoot: path, plan: null });
  }
  async prepare(): Promise<void> {
    if (this.running) return;
    await this.operation(async () => {
      if (!this.state.inputs.length || !this.state.outputRoot) throw new Error('请添加输入并选择结果目录。');
      const plan = await this.client.request<ProcessingPlan>('plans.create', {
        input_paths: this.state.inputs, output_root: this.state.outputRoot,
        options: this.state.settings?.settings.options,
      });
      this.startKey = crypto.randomUUID();
      this.update({ plan, job: null, logs: [], notice: `发现 ${plan.packages.length} 个文件组，确认后点击开始。` });
    });
  }
  async start(): Promise<void> {
    await this.operation(async () => {
      if (!this.state.plan || !this.startKey) throw new Error('请先扫描文件。');
      const job = await this.client.request<JobSnapshot>('jobs.start', {
        plan_id: this.state.plan.plan_id, idempotency_key: this.startKey,
      });
      this.update({ job, notice: '处理已开始。成品生成后会归档原包。' });
    });
  }
  /**
   * Delivered independently of the busy gate: a close request or toolbar click must not be
   * swallowed while another operation holds busy. It never writes or restores busy, so whichever
   * of cancel and the concurrent operation finishes last cannot leave the UI stuck busy; the
   * private in-flight flag suppresses duplicate delivery instead.
   */
  async cancel(): Promise<void> {
    const job = this.state.job;
    if (!job || this.cancelling) return;
    this.cancelling = true;
    this.update({ error: '' });
    try {
      const result = await this.client.request<{ accepted: boolean }>('jobs.cancel', { job_id: job.job_id });
      this.update({ notice: result.accepted
        ? '已请求取消，正在完成当前安全步骤。'
        : '任务已结束，无需取消。' });
      await this.refresh(true);
    } catch (error) { this.update({ error: error instanceof Error ? error.message : String(error) }); }
    finally { this.cancelling = false; }
  }
  async refresh(force = false): Promise<void> {
    if (!this.state.ready) { await this.initialize(); return; }
    const job = this.state.job;
    if (!job || this.polling || (this.state.busy && !force) || (!force && terminalJobs.has(job.state))) return;
    this.polling = true;
    try {
      const latest = await this.client.request<JobSnapshot>('jobs.get', { job_id: job.job_id });
      // A response for an old selection must not replace a newer selected task.
      if (this.state.job?.job_id === job.job_id) this.update({ job: latest });
      if (terminalJobs.has(latest.state)) {
        const history = await this.client.request<JobSnapshot[]>('jobs.list', { limit: 20 });
        this.update({ history });
      }
    } catch (error) { this.update({ error: String(error) }); }
    finally { this.polling = false; }
  }
  async loadHistory(): Promise<void> {
    if (!this.state.ready) { await this.initialize(); return; }
    await this.operation(async () => {
      const history = await this.client.request<JobSnapshot[]>('jobs.list', { limit: 20 });
      const active = history.find(j => !terminalJobs.has(j.state));
      // jobs.list truncates packages[:5]; re-read the full snapshot before showing it.
      const job = active ? await this.client.request<JobSnapshot>('jobs.get', { job_id: active.job_id }) : null;
      this.update({ history, ...(job ? { job } : {}) });
    });
  }
  selectJob(job: JobSnapshot): void { if (!this.running) this.update({ job, plan: null, logs: [] }); }
  async selectHistoryJob(job: JobSnapshot): Promise<void> {
    if (this.running) return;
    await this.operation(async () => {
      this.selectJob(await this.client.request<JobSnapshot>('jobs.get', { job_id: job.job_id }));
    });
  }
  async retry(): Promise<void> {
    const job = this.state.job;
    if (!job || this.running) return;
    const ids = job.packages.filter(p => retryable.has(p.state)).map(p => p.package_id);
    await this.operation(async () => {
      if (!ids.length) throw new Error('没有可重试的文件组。需要检查的结果请先人工核对。');
      const next = await this.client.request<JobSnapshot>('jobs.retry', {
        job_id: job.job_id, package_ids: ids, idempotency_key: crypto.randomUUID(),
      });
      this.update({ job: next, logs: [] });
    });
  }
  async saveSettings(settings: DesktopSettings): Promise<void> {
    if (this.running) return;
    await this.operation(async () => {
      const result = await this.client.request<SettingsInfo>('settings.update', { ...settings });
      this.update({ settings: result, plan: null, notice: '设置已保存，请重新扫描。' });
    });
  }
  async replacePasswords(passwords: string[]): Promise<void> {
    await this.passwordAction('passwords.replace', { passwords });
  }
  async importPasswords(path: string): Promise<void> {
    await this.passwordAction('passwords.import', { path });
  }
  private async passwordAction(method: 'passwords.replace' | 'passwords.import', params: Record<string, unknown>): Promise<void> {
    if (this.running) return;
    await this.operation(async () => {
      await this.client.request(method, params);
      this.update({ settings: await this.client.request<SettingsInfo>('settings.get'), notice: '密码集已更新。' });
    });
  }
  async showLogs(): Promise<void> {
    const job = this.state.job;
    if (!job) return;
    await this.operation(async () => {
      const result = await this.client.request<{ lines: string[] }>('jobs.logs', { job_id: job.job_id, cursor: 0, limit: 200 });
      this.update({ logs: result.lines });
    });
  }
  async openResult(path?: string): Promise<void> {
    const job = this.state.job;
    if (!job) return;
    await this.operation(async () => { await this.client.openResult(job.job_id, path); });
  }
}
