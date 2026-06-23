param(
  [string]$Version = "",
  [string]$CondaEnv = "reorder",
  [string]$OutputRoot = "dist",
  [string]$AppName = "reorder-extract-windows",
  [string]$ReleaseRoot = "releases",
  [switch]$SkipTests,
  [switch]$SkipSmoke
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
if (-not $Version) {
  $Version = Get-Date -Format "yyyyMMdd-HHmmss"
}

$appDir = Join-Path $repo (Join-Path $OutputRoot $AppName)
$releaseDir = Join-Path $repo $ReleaseRoot
$stagingRoot = Join-Path $repo "build\release-staging"
$packageName = "$AppName-$Version"
$packageDir = Join-Path $stagingRoot $packageName
$zipPath = Join-Path $releaseDir "$packageName.zip"

function Invoke-ProjectPython {
  param([string[]]$Arguments)
  if ($CondaEnv) {
    & conda run -n $CondaEnv python @Arguments
  } else {
    & python @Arguments
  }
  if ($LASTEXITCODE -ne 0) {
    throw "Python command failed with exit code $LASTEXITCODE"
  }
}

function Write-Utf8NoBom {
  param(
    [Parameter(Mandatory = $true)][string]$Path,
    [Parameter(Mandatory = $true)][string]$Content
  )
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($Path, $Content, $utf8NoBom)
}

Push-Location $repo
try {
  if (-not $SkipTests) {
    $env:PYTHONPATH = Join-Path $repo "src"
    Invoke-ProjectPython @("-m", "unittest", "discover", "-s", "tests")
  }

  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repo "scripts\build_windows_dist.ps1") `
    -OutputRoot $OutputRoot `
    -Name $AppName `
    -CondaEnv $CondaEnv `
    -Version $Version
  if ($LASTEXITCODE -ne 0) {
    throw "Build failed with exit code $LASTEXITCODE"
  }

  & (Join-Path $appDir "reorder-extract.exe") --prepare-tools --self-check
  if ($LASTEXITCODE -ne 0) {
    throw "Portable self-check failed with exit code $LASTEXITCODE"
  }

  if (-not $SkipSmoke) {
    $smoke = Join-Path $repo "build\portable-release-smoke"
    Remove-Item -LiteralPath $smoke -Recurse -Force -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path $smoke | Out-Null
    Set-Content -LiteralPath (Join-Path $smoke "payload.txt") -Value "release-smoke" -Encoding ASCII
    & (Join-Path $appDir "tools\7zip\7z.exe") a (Join-Path $smoke "sample.zip") (Join-Path $smoke "payload.txt") | Out-Null
    if ($LASTEXITCODE -ne 0) {
      throw "Smoke archive creation failed with exit code $LASTEXITCODE"
    }
    Remove-Item -LiteralPath (Join-Path $smoke "payload.txt") -Force
    & (Join-Path $appDir "reorder-extract.exe") --folder $smoke --disable-bandizip
    if ($LASTEXITCODE -ne 0) {
      throw "Portable smoke extraction failed with exit code $LASTEXITCODE"
    }
    $expected = Get-ChildItem -LiteralPath (Join-Path $smoke "final") -Recurse -File -Filter "payload.txt" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $expected) {
      throw "Portable smoke extraction did not create payload.txt under final/"
    }
  }

  New-Item -ItemType Directory -Force -Path $releaseDir, $stagingRoot | Out-Null
  Remove-Item -LiteralPath $packageDir -Recurse -Force -ErrorAction SilentlyContinue
  Copy-Item -LiteralPath $appDir -Destination $packageDir -Recurse -Force

  $manifest = @"
name=$AppName
version=$Version
built_at=$(Get-Date -Format "yyyy-MM-dd HH:mm:ss")
conda_env=$CondaEnv
source=$repo
"@
  Write-Utf8NoBom -Path (Join-Path $packageDir "RELEASE.txt") -Content ($manifest + [Environment]::NewLine)

  Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
  $sevenZip = Join-Path $appDir "tools\7zip\7z.exe"
  $zipped = $false
  for ($attempt = 1; $attempt -le 5; $attempt++) {
    try {
      Push-Location $stagingRoot
      & $sevenZip a -tzip $zipPath $packageName | Out-Null
      if ($LASTEXITCODE -ne 0) {
        throw "7z failed with exit code $LASTEXITCODE"
      }
      $zipped = $true
      break
    }
    catch {
      if ($attempt -eq 5) {
        throw
      }
      Start-Sleep -Seconds 2
      Remove-Item -LiteralPath $zipPath -Force -ErrorAction SilentlyContinue
    }
    finally {
      Pop-Location
    }
  }
  if (-not $zipped) {
    throw "Release ZIP was not created"
  }

  $bytes = (Get-Item -LiteralPath $zipPath).Length
  Write-Host "Release ZIP: $zipPath"
  Write-Host ("Release ZIP size: {0:N2} MB" -f ($bytes / 1MB))
}
finally {
  Pop-Location
}
