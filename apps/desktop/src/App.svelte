<script lang="ts">
  import { onMount } from 'svelte';
  import { Dialog } from 'bits-ui';
  import { FileArchive, FolderPlus, FolderOpen, Settings2, ScanLine, Play, Square, RotateCcw, Upload, ShieldCheck, Layers3, History, X, Palette, ImagePlus, ImageOff, Sparkles, Info } from '@lucide/svelte';
  import { isTauri } from '@tauri-apps/api/core';
  import { getCurrentWebview } from '@tauri-apps/api/webview';
  import { getCurrentWindow } from '@tauri-apps/api/window';
  import { open, confirm } from '@tauri-apps/plugin-dialog';
  import { DesktopController, type DesktopState } from './lib/desktop-controller';
  import { TauriEngineClient } from './lib/engine-client';
  import { terminalJobs, terminalPackages, retryable } from './lib/contracts';
  import type { DesktopSettings, ProcessingOptions, SettingsInfo } from './lib/contracts';
  import {
    MAX_IMAGE_BYTES, applyAppearance, clampBlur, clampOverlay, defaultAppearance, loadAppearance,
    readLocalImage, saveAppearance, normalizeAppearance,
    type AppearanceSettings, type BackgroundMode,
  } from './lib/appearance';

  const controller = new DesktopController(new TauriEngineClient());
  let view = $state<DesktopState>({ ready: false, busy: false, error: '', notice: '', inputs: [], outputRoot: '', settings: null, info: null, plan: null, job: null, history: [], logs: [] });
  let settingsOpen = $state(false);
  let draft = $state<DesktopSettings | null>(null);
  let passwordText = $state('');
  let dragging = $state(false);
  let dialogError = $state('');

  // Appearance is independent of the business run and never touches the engine.
  let committed = $state<AppearanceSettings>({ ...defaultAppearance });
  let appearanceDraft = $state<AppearanceSettings>({ ...defaultAppearance });
  let appearanceOpen = $state(false);
  let appearanceError = $state('');
  let appearanceNotice = $state('');
  let imageInfo = $state('');
  let imageBusy = $state(false);

  const running = $derived(!!view.job && !terminalJobs.has(view.job.state));
  const done = $derived(view.job?.packages.filter(p => terminalPackages.has(p.state)).length ?? 0);
  const total = $derived(view.job?.packages.length ?? view.plan?.packages.length ?? 0);
  const retryCount = $derived(view.job?.packages.filter(p => retryable.has(p.state)).length ?? 0);
  const names: Record<string, string> = { queued: '等待', running: '处理中', preparing: '准备副本', extracting: '恢复 / 解压', publishing: '发布成品', archiving: '归档原包', cancelling: '正在取消', succeeded: '完成', partial: '部分完成', failed: '失败', deferred: '等待补卷', cancelled: '已取消', interrupted: '已中断', needs_review: '需要检查' };
  const size = (bytes: number) => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : `${(bytes / 1024 ** 2).toFixed(1)} MB`;
  const formatBytes = (bytes: number) => bytes >= 1024 * 1024 ? `${(bytes / 1024 / 1024).toFixed(2)} MiB` : `${Math.max(1, Math.round(bytes / 1024))} KiB`;
  const MB_LIMIT = MAX_IMAGE_BYTES / 1024 / 1024;

  // The engine's settings schema grows these two switches; read and write them defensively so the
  // screen stays valid whether or not the contracts update has landed yet.
  type BuiltinFlag = 'use_builtin_passwords' | 'clean_builtin_keywords';
  type OptionsWithBuiltins = ProcessingOptions & Partial<Record<BuiltinFlag, boolean>>;
  function builtinFlag(options: ProcessingOptions, key: BuiltinFlag, fallback: boolean): boolean {
    const value = (options as OptionsWithBuiltins)[key];
    return typeof value === 'boolean' ? value : fallback;
  }
  function setBuiltinFlag(target: DesktopSettings | null, key: BuiltinFlag, value: boolean): void {
    if (!target) return;
    (target.options as OptionsWithBuiltins)[key] = value;
  }
  interface SettingsDefaults { password_count?: number; keyword_count?: number; passwords_enabled?: boolean; keyword_cleaning_enabled?: boolean; version?: string }
  function defaultCount(key: 'password_count' | 'keyword_count'): number {
    const defaults = (view.settings as (SettingsInfo & { defaults?: SettingsDefaults }) | null)?.defaults;
    const value = defaults?.[key];
    return typeof value === 'number' ? value : 0;
  }

  function storage(): Storage | null {
    try { return typeof window === 'undefined' ? null : window.localStorage; } catch { return null; }
  }
  function applyNow(settings: AppearanceSettings): void {
    try { applyAppearance(document.documentElement, settings); } catch { /* keep the solid fallback background */ }
  }
  function openAppearance(): void {
    appearanceDraft = { ...committed };
    appearanceError = ''; appearanceNotice = ''; imageInfo = '';
    appearanceOpen = true;
  }
  function closeAppearance(): void {
    appearanceDraft = { ...committed };
    appearanceError = ''; appearanceNotice = ''; imageInfo = '';
    applyNow(committed);
  }
  function previewAppearance(): void { applyNow(appearanceDraft); }
  function setMode(mode: BackgroundMode): void {
    appearanceError = ''; appearanceNotice = '';
    appearanceDraft.mode = mode;
    applyNow(appearanceDraft);
  }
  function setOverlay(event: Event): void {
    appearanceDraft.overlay = clampOverlay((event.currentTarget as HTMLInputElement).value);
    applyNow(appearanceDraft);
  }
  function setBlur(event: Event): void {
    appearanceDraft.blur = clampBlur((event.currentTarget as HTMLInputElement).value);
    applyNow(appearanceDraft);
  }
  async function pickBackground(event: Event): Promise<void> {
    const input = event.currentTarget as HTMLInputElement;
    const file = input.files?.[0];
    input.value = '';
    if (!file) return;
    imageBusy = true; appearanceError = ''; appearanceNotice = ''; imageInfo = '';
    try {
      const result = await readLocalImage(file);
      if (!result.ok) { appearanceError = result.reason; return; }
      appearanceDraft.mode = 'custom';
      appearanceDraft.dataUrl = result.dataUrl;
      imageInfo = `${result.width} × ${result.height} · ${formatBytes(file.size)}`;
      appearanceNotice = '已载入本机图片，保存后长期生效。';
      applyNow(appearanceDraft);
    } catch { appearanceError = '读取图片失败，请重试。'; }
    finally { imageBusy = false; }
  }
  function resetAppearance(): void {
    appearanceError = ''; appearanceNotice = ''; imageInfo = '';
    appearanceDraft = { ...defaultAppearance };
    applyNow(appearanceDraft);
  }
  function saveAndClose(): void {
    const normalized = normalizeAppearance(appearanceDraft);
    const result = saveAppearance(storage(), normalized);
    if (result.ok) {
      committed = normalized;
      appearanceError = ''; appearanceNotice = ''; imageInfo = '';
      appearanceOpen = false;
      return;
    }
    // Keep the previous selection visible rather than showing an unsaved state.
    appearanceNotice = '';
    appearanceError = result.reason;
    appearanceDraft = { ...committed };
    applyNow(committed);
  }

  async function pick(kind: 'files' | 'folder' | 'output') {
    try {
      const value = await open({ directory: kind !== 'files', multiple: kind === 'files', title: kind === 'output' ? '选择结果目录' : '添加待处理文件' });
      if (!value) return;
      if (kind === 'output') controller.setOutput(value as string);
      else controller.addInputs(Array.isArray(value) ? value : [value]);
    } catch (error) { dialogError = String(error); }
  }
  function editSettings() {
    if (!view.settings) return;
    draft = structuredClone($state.snapshot(view.settings.settings)); settingsOpen = true;
  }
  function clearPendingPasswords() { passwordText = ''; dialogError = ''; }
  async function pickTool(key: 'seven_zip' | 'unrar' | 'bandizip') {
    try {
      const value = await open({ multiple: false, directory: false, title: '选择本机解压工具' });
      if (typeof value === 'string' && draft) draft.tools[key] = value;
    } catch (error) { dialogError = String(error); }
  }
  async function importPasswords() {
    try {
      const value = await open({ multiple: false, filters: [{ name: 'UTF-8 文本', extensions: ['txt'] }] });
      if (typeof value === 'string') await controller.importPasswords(value);
    } catch (error) { dialogError = String(error); }
  }
  onMount(() => {
    const unsubscribe = controller.subscribe(value => { view = value; });
    let stopped = false;
    const cleanups: (() => void)[] = [];
    const stored = loadAppearance(storage());
    committed = stored;
    appearanceDraft = { ...stored };
    applyNow(stored);
    void controller.initialize();
    if (isTauri()) {
      void getCurrentWebview().onDragDropEvent(event => {
        dragging = event.payload.type === 'over';
        if (event.payload.type === 'drop') { dragging = false; controller.addInputs(event.payload.paths); }
      }).then(cleanup => { if (stopped) cleanup(); else cleanups.push(cleanup); });
      void getCurrentWindow().onCloseRequested(async event => {
        if (!controller.running) return;
        event.preventDefault();
        const accepted = await confirm('任务仍在处理。取消后请等待文件安全收尾，再关闭软件。', { title: '正在处理', kind: 'warning', okLabel: '取消任务', cancelLabel: '继续处理' });
        if (accepted) await controller.cancel();
      }).then(cleanup => { if (stopped) cleanup(); else cleanups.push(cleanup); });
    }
    return () => { stopped = true; unsubscribe(); controller.dispose(); cleanups.forEach(fn => fn()); };
  });
</script>

<div class="hr-backdrop" aria-hidden="true"></div>
<div class="hr-scrim" aria-hidden="true"></div>

<div class="shell">
  <header><div class="brand"><span class="mark">星</span><div><h1>星绫解封 <span>· Hoshiribbon</span></h1><p>把压缩的次元，一层层展开。</p></div></div><div class="header-actions"><button class="quiet" onclick={openAppearance}><Palette size={16} />外观</button><button class="quiet icon-button" onclick={editSettings} disabled={!view.ready || running}><Settings2 size={16} />设置</button></div></header>
  <main>
    <div class="workspace-heading"><div><span class="eyebrow">LOCAL WORKSPACE</span><h2>让复杂归档，回到简单。</h2><p>所有处理都在本机完成，按文件组安全整理。</p></div><span class="offline-badge"><ShieldCheck size={14} /> 本地处理</span></div>
    <div class="overview"><div><span class="overview-icon"><FileArchive size={19} /></span><div><span>待处理文件组</span><strong>{total || '—'}</strong></div></div><div><span class="overview-icon"><Layers3 size={19} /></span><div><span>处理模式</span><strong>{view.settings?.settings.options.deep_extract ? '深度解压' : '单层解压'}</strong></div></div><div><span class="overview-icon"><ShieldCheck size={19} /></span><div><span>原包归档</span><strong>校验后移动</strong></div></div></div>
    <section class="input-card" class:dragging>
      <div class="section-heading"><h2>01 <span>添加文件</span></h2><span class="muted">可拖入文件或文件夹</span></div>
      <div class="input-actions"><button onclick={() => pick('files')} disabled={running || view.busy}><FileArchive size={16} />添加文件</button><button onclick={() => pick('folder')} disabled={running || view.busy}><FolderPlus size={16} />添加文件夹</button></div>
      {#if !view.inputs.length}<div class="empty"><span class="upload-icon"><Upload size={24} /></span>把待处理的压缩包拖到这里<br /><span>支持分卷、伪装后缀、合并文件恢复和加密归档</span></div>{:else}<ul class="path-list">{#each view.inputs as path}<li><span title={path}>{path}</span><button class="text-button" onclick={() => controller.removeInput(path)} disabled={running}>移除</button></li>{/each}</ul>{/if}
      <div class="output-row"><label for="output">结果目录</label><input id="output" readonly value={view.outputRoot} placeholder="选择保存成品和原包的位置" /><button onclick={() => pick('output')} disabled={running || view.busy}>选择</button></div>
      <p class="hint">完成后，成品放入 final，原包移动到 success/archives。失败和缺卷会单独分类；同名文件不会覆盖。</p>
    </section>

    {#if view.error || dialogError}<div role="alert" class="notice error">{view.error || dialogError}<button class="text-button" onclick={() => { dialogError = ''; void controller.loadHistory(); }}>刷新任务</button></div>{/if}
    {#if view.notice}<div role="status" class="notice">{view.notice}</div>{/if}

    <section class="jobs-card">
      <div class="section-heading"><h2>02 <span>{view.job ? '处理结果' : '确认并处理'}</span></h2><div class="toolbar"><button onclick={() => controller.prepare()} disabled={!view.ready || view.busy || running || !view.inputs.length || !view.outputRoot}><ScanLine size={16} />扫描</button>{#if running}<button class="danger" onclick={() => controller.cancel()} disabled={view.job?.state === 'cancelling'}><Square size={14} />取消</button>{:else if view.job}<button onclick={() => controller.retry()} disabled={view.busy || !retryCount}><RotateCcw size={14} />重试 {retryCount ? `(${retryCount})` : ''}</button>{:else}<button class="primary" onclick={() => controller.start()} disabled={view.busy || !view.plan}><Play size={15} />开始处理</button>{/if}</div></div>
      {#if view.job}<div class="progress-heading"><span class="status" data-state={view.job.state}>{names[view.job.state]}</span><span class="muted">{done} / {total} 个文件组</span><button class="text-button" onclick={() => controller.openResult()} disabled={view.busy}><FolderOpen size={14} />打开结果目录</button></div><progress max={Math.max(total, 1)} value={done}></progress>{/if}
      <div class="table-wrap"><table><thead><tr><th>文件组</th><th>状态 / 大小</th><th>说明</th></tr></thead><tbody>
        {#if view.job}{#each view.job.packages as item}<tr><td title={item.name}>{item.name}</td><td><span class="status" data-state={item.state}>{names[item.state]}</span></td><td class="detail" title={item.message}>{item.message || item.error_code || '—'}</td></tr>{/each}
        {:else if view.plan}{#each view.plan.packages as item}<tr><td title={item.name}>{item.name}</td><td>{size(item.bytes)}</td><td>{item.members} 个成员</td></tr>{/each}
        {:else}<tr><td colspan="3" class="table-empty">添加文件，扫描后查看分组，再开始处理。</td></tr>{/if}
      </tbody></table></div>
      {#if view.plan}<div class="warnings">{#each view.plan.warnings as warning}<p>{warning}</p>{/each}</div>{/if}
      {#if view.job}<details><summary onclick={() => controller.showLogs()}>查看处理记录</summary><pre>{view.logs.join('\n') || '暂无记录。'}</pre></details>{/if}
    </section>
    <section class="history"><div class="section-heading"><h2 class="icon-label"><History size={15} />最近任务</h2><button class="text-button" onclick={() => controller.loadHistory()} disabled={view.busy}>刷新</button></div><div class="history-list">{#each view.history as job}<button class="history-item" onclick={() => controller.selectHistoryJob(job)} disabled={running}><span>{new Date(job.created_at).toLocaleString()}</span><span>{job.package_count ?? job.packages.length} 组 · {names[job.state]}</span></button>{:else}<p class="muted">处理记录保存在本机。</p>{/each}</div></section>
    <section class="support"><details><summary class="icon-label"><Info size={14} />支持方式说明</summary><p>支持的归档：ZIP、7z、RAR 及常见分卷。可尝试恢复伪装后缀、合并分片与 Apate 伪装文件。</p><p class="muted">以上为尽力恢复，个别文件不能保证 100% 成功；完整对照见用户指南。</p></details></section>
  </main>
  <footer><span>{view.info ? `${view.info.platform} · v${view.info.version}` : '跨平台桌面工具'}</span><span>本地处理 · 用户密码 {view.settings?.passwords.count ?? 0} 个</span></footer>
</div>

<Dialog.Root bind:open={appearanceOpen} onOpenChange={(open) => { if (!open) closeAppearance(); }}>
<Dialog.Portal><Dialog.Overlay class="modal-backdrop" /><Dialog.Content class="settings-dialog appearance-dialog"><div class="section-heading"><Dialog.Title class="settings-title">外观</Dialog.Title><Dialog.Close class="quiet icon-button" aria-label="关闭外观设置"><X size={17} /></Dialog.Close></div><Dialog.Description class="hint">外观只保存在本机，不影响正在进行的处理。</Dialog.Description>
  <div class="mode-row" role="group" aria-label="背景模式">
    <button class:active={appearanceDraft.mode === 'default'} onclick={() => setMode('default')}><Sparkles size={15} />默认背景</button>
    <button class:active={appearanceDraft.mode === 'custom'} onclick={() => setMode('custom')}><ImagePlus size={15} />自定义图片</button>
    <button class:active={appearanceDraft.mode === 'none'} onclick={() => setMode('none')}><ImageOff size={15} />无背景</button>
  </div>
  {#if appearanceDraft.mode === 'custom'}<div class="file-row"><label class="file-button">选择本机图片<input type="file" accept="image/png,image/jpeg,image/webp" onchange={pickBackground} disabled={imageBusy} /></label><span class="muted">{imageBusy ? '正在读取…' : imageInfo || `PNG / JPEG / WebP，最大 ${MB_LIMIT} MiB`}</span></div>{/if}
  <div class="sliders"><label>遮罩强度 <span class="muted">{appearanceDraft.overlay}%</span><input type="range" min="0" max="100" value={appearanceDraft.overlay} oninput={setOverlay} /></label><label>模糊度 <span class="muted">{appearanceDraft.blur}px</span><input type="range" min="0" max="24" value={appearanceDraft.blur} oninput={setBlur} /></label></div>
  {#if appearanceError}<p role="alert" class="error">{appearanceError}</p>{/if}
  {#if appearanceNotice}<p role="status" class="hint">{appearanceNotice}</p>{/if}
  <div class="dialog-footer"><button class="text-button" onclick={resetAppearance}>恢复默认</button><button class="text-button" onclick={previewAppearance}>预览</button><button class="primary" onclick={saveAndClose} disabled={imageBusy}>保存外观</button></div>
</Dialog.Content></Dialog.Portal>
</Dialog.Root>

<Dialog.Root bind:open={settingsOpen} onOpenChange={(open) => { if (!open) clearPendingPasswords(); }}>
{#if draft}<Dialog.Portal><Dialog.Overlay class="modal-backdrop" /><Dialog.Content class="settings-dialog"><div class="section-heading"><Dialog.Title class="settings-title">处理设置</Dialog.Title><Dialog.Close class="quiet icon-button" aria-label="关闭设置"><X size={17} /></Dialog.Close></div><Dialog.Description class="hint">设置会保存在本机。修改后请重新扫描文件。</Dialog.Description>
  <div class="settings-grid">
    <label class="check"><input type="checkbox" bind:checked={draft.options.deep_extract} />继续解开嵌套压缩包</label><label class="check"><input type="checkbox" bind:checked={draft.options.recursive} />扫描输入的子目录</label>
    <label class="check"><input type="checkbox" bind:checked={draft.options.preserve_payload_names} />保留内容原名</label><label class="check"><input type="checkbox" bind:checked={draft.options.keep_workspace} />保留临时工作区</label>
    <label>最大解压层数<input type="number" min="1" max="20" bind:value={draft.options.max_depth} /></label><label>输出空间上限 (GB)<input type="number" min="1" bind:value={draft.options.max_output_gb} /></label>
    <label>工具超时 (秒)<input type="number" min="1" max="86400" bind:value={draft.options.tool_timeout_sec} /></label><label>嵌套归档识别下限 (MB)<input type="number" min="1" bind:value={draft.options.min_archive_mb} /></label>
    <label>单文件成品阈值 (MB)<input type="number" min="1" bind:value={draft.options.final_single_mb} /></label>
  </div>
  <h3>内置库与关键词</h3>
  <p class="hint">内置密码库 {defaultCount('password_count')} 个 · 用户库 {view.settings?.passwords.count ?? 0} 个 · 内置关键词 {defaultCount('keyword_count')} 条</p>
  <div class="settings-grid">
    <label class="check"><input type="checkbox" checked={builtinFlag(draft.options, 'use_builtin_passwords', true)} onchange={(event) => setBuiltinFlag(draft, 'use_builtin_passwords', event.currentTarget.checked)} />使用内置密码库</label>
    <label class="check"><input type="checkbox" checked={builtinFlag(draft.options, 'clean_builtin_keywords', false)} onchange={(event) => setBuiltinFlag(draft, 'clean_builtin_keywords', event.currentTarget.checked)} />清理内置关键词</label>
  </div>
  <h3>解压工具</h3>{#each ['seven_zip', 'unrar', 'bandizip'] as raw}{@const key = raw as 'seven_zip' | 'unrar' | 'bandizip'}<div class="tool-row"><span>{key === 'seven_zip' ? '7-Zip' : key === 'unrar' ? 'UnRAR' : 'Bandizip'}</span><input aria-label={`${key} 路径`} bind:value={draft.tools[key]} placeholder={view.settings?.tools[key] || '自动查找本机工具'} /><button onclick={() => pickTool(key)}>选择</button></div>{/each}
  <p class="hint">7-Zip、UnRAR、Bandizip 均已随软件提供，无需额外安装；也可以指定本机已有路径。</p>
  <h3>密码集</h3><p class="hint">内置库 {defaultCount('password_count')} 个 · 用户库 {view.settings?.passwords.count ?? 0} 个 · {view.settings?.passwords.storage === 'system' ? '使用系统凭据保存' : '仅本次会话保存，退出后需重新导入'}。每行一个，只在本机使用，不会写入外观或其他本地存储。</p><textarea aria-label="归档密码，每行一个" bind:value={passwordText} spellcheck="false" rows="3" placeholder="在这里粘贴密码，或导入 UTF-8 文本"></textarea><div class="password-actions"><button onclick={importPasswords} disabled={view.busy}>导入密码文件</button><button onclick={async () => { await controller.replacePasswords(passwordText.split(/\r?\n/)); if (!view.error) passwordText = ''; }} disabled={view.busy || !passwordText}>替换密码集</button><button class="text-button" onclick={() => controller.replacePasswords([])} disabled={view.busy}>清空</button></div>
  {#if view.error}<p role="alert" class="error">{view.error}</p>{/if}<div class="dialog-footer"><button class="primary" onclick={async () => { if (draft) await controller.saveSettings($state.snapshot(draft)); if (!view.error) { passwordText = ''; settingsOpen = false; } }} disabled={view.busy}>保存设置</button></div>
</Dialog.Content></Dialog.Portal>{/if}
</Dialog.Root>
