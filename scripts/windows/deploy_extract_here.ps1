$ErrorActionPreference = 'Stop'
$repo = $env:REORDER_DEPLOY_REPO
$out = $env:REORDER_DEPLOY_OUTPUT
$parent = Split-Path -Parent $out
if (-not (Test-Path -LiteralPath $parent)) {
    [IO.Directory]::CreateDirectory($parent) | Out-Null
}

# The BAT reads these literals once. Tail delegation avoids CALL's second
# expansion of percent signs in paths and preserves the engine's exit code.
$escapedRepo = $repo.Replace('%', '%%')
$lines = @(
    '@echo off'
    'setlocal DisableDelayedExpansion'
    'chcp 65001 >nul'
    ('set "REORDER_WORK_DIR=' + $escapedRepo + '"')
    '"%REORDER_WORK_DIR%\scripts\windows\launcher.bat" extract "%~f0" %*'
)
$content = ($lines -join "`r`n") + "`r`n"
$created = $false
try {
    $stream = [IO.File]::Open($out, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
    $created = $true
    try {
        $bytes = [Text.UTF8Encoding]::new($false).GetBytes($content)
        $stream.Write($bytes, 0, $bytes.Length)
    } finally {
        $stream.Dispose()
    }
} catch {
    if ($created) { Remove-Item -LiteralPath $out -Force -ErrorAction SilentlyContinue }
    throw
}
