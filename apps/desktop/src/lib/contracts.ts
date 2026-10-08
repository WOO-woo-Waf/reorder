export type PackageState = 'queued' | 'preparing' | 'extracting' | 'publishing' | 'archiving' |
  'succeeded' | 'partial' | 'failed' | 'deferred' | 'cancelled' | 'interrupted' | 'needs_review';
export type JobState = 'queued' | 'running' | 'cancelling' | 'succeeded' | 'partial' | 'failed' | 'cancelled' | 'interrupted' | 'needs_review';
export interface ProcessingOptions {
  deep_extract: boolean; max_depth: number; min_archive_mb: number; final_single_mb: number;
  preserve_payload_names: boolean; recursive: boolean; tool_timeout_sec: number;
  max_output_gb: number; keep_workspace: boolean;
  use_builtin_passwords: boolean; clean_builtin_keywords: boolean;
}
export interface ToolPaths { seven_zip: string | null; unrar: string | null; bandizip: string | null }
export interface DesktopSettings { version: number; options: ProcessingOptions; tools: ToolPaths }
export interface SettingsInfo {
  settings: DesktopSettings; tools: ToolPaths;
  passwords: { count: number; storage: 'system' | 'session' };
  defaults?: { password_count: number; keyword_count: number; passwords_enabled: boolean;
    keyword_cleaning_enabled: boolean; version: string };
}
export interface SystemInfo extends SettingsInfo {
  version: string; protocol_version: number; platform: string; data_root: string; capabilities: string[];
}
export interface PlanPackage { package_id: string; name: string; members: number; bytes: number }
export interface ProcessingPlan { plan_id: string; output_root: string; warnings: string[]; packages: PlanPackage[] }
export interface PackageSnapshot {
  package_id: string; name: string; state: PackageState; message: string;
  error_code: string | null; results: string[];
}
export interface JobSnapshot {
  job_id: string; plan_id: string; state: JobState; created_at: string; updated_at: string;
  packages: PackageSnapshot[]; last_seq: number; retry_of: string | null;
  package_count?: number;
}
export type Method = 'system.info' | 'plans.create' | 'jobs.start' | 'jobs.get' | 'jobs.list' |
  'jobs.cancel' | 'jobs.retry' | 'jobs.events' | 'jobs.logs' | 'settings.get' | 'settings.update' |
  'passwords.replace' | 'passwords.import' | 'results.get';
export const terminalJobs = new Set<JobState>(['succeeded', 'partial', 'failed', 'cancelled', 'interrupted', 'needs_review']);
export const terminalPackages = new Set<PackageState>(['succeeded', 'partial', 'failed', 'deferred', 'cancelled', 'interrupted', 'needs_review']);
export const retryable = new Set<PackageState>(['failed', 'deferred', 'cancelled', 'interrupted']);
