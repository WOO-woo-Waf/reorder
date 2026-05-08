param(
    [string]$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path,
    [string]$OutDir = ""
)

$ErrorActionPreference = "Stop"

$repo = (Resolve-Path $RepoRoot).Path
if ([string]::IsNullOrWhiteSpace($OutDir)) {
    $OutDir = Join-Path $repo "dist"
}

if ([System.IO.Path]::IsPathRooted($OutDir)) {
    $out = $OutDir
} else {
    $out = Join-Path $repo $OutDir
}

$stage = Join-Path $out "_reorder-engine-skill"
$zip = Join-Path $out "reorder-engine-skill.zip"

if (Test-Path -LiteralPath $stage) {
    $resolvedStage = (Resolve-Path -LiteralPath $stage).Path
    $resolvedOut = (Resolve-Path -LiteralPath $out).Path
    if (-not $resolvedStage.StartsWith($resolvedOut, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove stage outside output directory: $resolvedStage"
    }
    Remove-Item -LiteralPath $stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $stage | Out-Null
New-Item -ItemType Directory -Force -Path $out | Out-Null

$include = @(
    ".agents\skills\reorder-engine",
    "src",
    "tests",
    "resources",
    "config.json",
    "tools\README.md",
    "tools\APATE.md",
    "tools\apate.py",
    "README.md",
    "pyproject.toml",
    "requirements.txt",
    "prepare_extract_tools.bat",
    "beta_here.bat",
    "cleanup_extract_artifacts.bat"
)

foreach ($relative in $include) {
    $source = Join-Path $repo $relative
    if (-not (Test-Path -LiteralPath $source)) {
        continue
    }
    $target = Join-Path $stage $relative
    $targetParent = Split-Path -Parent $target
    New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
    Copy-Item -LiteralPath $source -Destination $target -Recurse -Force
}

$excludedSamples = @(
    "tests\requirements.zip"
)
foreach ($relative in $excludedSamples) {
    $sample = Join-Path $stage $relative
    if (Test-Path -LiteralPath $sample) {
        Remove-Item -LiteralPath $sample -Force
    }
}

$installReadme = Join-Path $stage "INSTALL_SKILL.md"
@"
# Reorder Engine Skill Package

This package contains the `reorder-engine` Codex skill plus the project code it expects.

Install the skill on Windows:

```powershell
Copy-Item -Recurse .agents\skills\reorder-engine "`$env:USERPROFILE\.codex\skills\reorder-engine"
```

Prepare extractor tools:

```powershell
.\prepare_extract_tools.bat
```

Run tests from this package root:

```powershell
`$env:PYTHONPATH="`$PWD\src"
python -m unittest discover -s tests
```

If you add private RAR/Bandizip bundles, place them under `tools\_packages\` and run `prepare_extract_tools.bat`.
"@ | Set-Content -LiteralPath $installReadme -Encoding UTF8

if (Test-Path -LiteralPath $zip) {
    Remove-Item -LiteralPath $zip -Force
}
Compress-Archive -Path (Join-Path $stage "*") -DestinationPath $zip -Force

Write-Host "Wrote $zip"
