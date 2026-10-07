param(
  [Parameter(Mandatory = $true)][string]$Exe,
  [Parameter(Mandatory = $true)][string]$Evidence
)
$ErrorActionPreference = "Stop"
$binary = (Resolve-Path $Exe).Path
$directory = Split-Path -Parent $binary
$data = Join-Path $directory "data"
if (Test-Path $data) { throw "Use a fresh preview directory: existing portable data must be preserved." }
$existing = @(Get-Process -Name "ReOrder", "reorder-desktop" -ErrorAction SilentlyContinue)
if ($existing.Count -gt 0) { throw "Close the existing ReOrder instance manually before this isolated smoke check." }
$record = [ordered]@{
  scope = "Windows desktop boot, portable engine startup, idle window close only"
  exe = $binary
  manual_acceptance = "Real files, drag/drop, settings interaction, install/uninstall and user experience pending"
  exit_code = 1
}
$owned = $null
$engineProcesses = @()
try {
  $owned = Start-Process -FilePath $binary -ArgumentList "--portable" -WorkingDirectory $directory -PassThru
  $null = $owned.Handle
  $deadline = [DateTime]::UtcNow.AddSeconds(30)
  do {
    Start-Sleep -Milliseconds 250
    $owned.Refresh()
    if ($owned.HasExited) { throw "Desktop exited before becoming ready: $($owned.ExitCode)" }
    $windowReady = $owned.MainWindowHandle -ne 0 -and $owned.MainWindowTitle -match "ReOrder"
    $engineReady = Test-Path (Join-Path $data "jobs.sqlite3")
  } while ((!$windowReady -or !$engineReady) -and [DateTime]::UtcNow -lt $deadline)
  if (!$windowReady -or !$engineReady) { throw "Desktop window or portable engine did not become ready." }
  $record.window_title = $owned.MainWindowTitle
  $record.portable_database = $engineReady
  $record.responding = $owned.Responding
  $children = Get-CimInstance Win32_Process -Filter "ParentProcessId = $($owned.Id)"
  $engineProcesses = @($children | Where-Object { $_.Name -eq "reorder-engine.exe" } | ForEach-Object {
    $child = Get-Process -Id $_.ProcessId -ErrorAction Stop
    $null = $child.Handle
    if ($child.ProcessName -ne "reorder-engine" -or $child.StartTime -lt $owned.StartTime) {
      throw "Engine process identity changed before it could be captured."
    }
    $child
  })
  $record.engine_child_count = $engineProcesses.Count
  if ($engineProcesses.Count -ne 1) { throw "Expected exactly one owned Python engine." }
  if (!$owned.CloseMainWindow()) { throw "Could not send the idle close request." }
  if (!$owned.WaitForExit(15000)) { throw "Idle close did not complete within 15 seconds." }
  $record.window_close_exit = $owned.ExitCode
  if ($owned.ExitCode -ne 0) { throw "Desktop close returned a failure." }
  foreach ($child in $engineProcesses) {
    if (!$child.HasExited -and !$child.WaitForExit(5000)) { throw "Owned engine remained after desktop exit." }
  }
  $record.owned_engine_stopped = $true
  $record.exit_code = 0
} catch {
  $record.error = $_.Exception.Message
} finally {
  # Retained handles identify these original processes even if a PID is reused.
  foreach ($process in $engineProcesses + @($owned)) {
    if ($process) {
      try {
        if (!$process.HasExited) { $process.Kill(); $null = $process.WaitForExit(5000) }
      } catch { $record.cleanup_error = $_.Exception.Message }
      $process.Dispose()
    }
  }
  $parent = Split-Path -Parent $Evidence
  New-Item -ItemType Directory -Force $parent | Out-Null
  [IO.File]::WriteAllText($Evidence, ($record | ConvertTo-Json -Depth 5), [Text.UTF8Encoding]::new($false))
}
$record | ConvertTo-Json -Depth 5
exit $record.exit_code
