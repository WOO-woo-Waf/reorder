param(
  [string]$ZipPath = "",
  [string]$Tag = "",
  [string]$Title = "",
  [switch]$Draft,
  [switch]$Prerelease,
  [switch]$Clobber
)

$ErrorActionPreference = "Stop"

$repo = Resolve-Path (Join-Path $PSScriptRoot "..")
$releaseRoot = Join-Path $repo "releases"

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
  throw "GitHub CLI 'gh' is not installed. Install it first: https://cli.github.com/"
}

& gh auth status
if ($LASTEXITCODE -ne 0) {
  throw "GitHub CLI is not authenticated. Run: gh auth login"
}

if (-not $ZipPath) {
  $latest = Get-ChildItem -LiteralPath $releaseRoot -Filter "reorder-extract-windows-*.zip" -File |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (-not $latest) {
    throw "No release ZIP found under $releaseRoot. Run scripts\release_windows_dist.ps1 first."
  }
  $ZipPath = $latest.FullName
}

$zip = Resolve-Path $ZipPath
$zipFile = Get-Item -LiteralPath $zip

if (-not $Tag) {
  $baseName = [System.IO.Path]::GetFileNameWithoutExtension($zipFile.Name)
  $version = $baseName -replace "^reorder-extract-windows-", ""
  $Tag = "portable-$version"
}

if (-not $Title) {
  $Title = "Reorder Extract Windows $Tag"
}

$body = @"
Windows portable release for Reorder Extract.

Download the ZIP asset, extract it, then run extract_here.bat.

The ZIP contains:
- reorder-extract.exe
- _internal Python runtime files
- tools/7zip, tools/rar, tools/bandizip
- resources/passwords.txt and resources/keywords.txt
- extract_here.bat and target_folder.txt

See docs/windows_portable_release.md and docs/github_release_download.md for usage.
"@

$args = @("release", "create", $Tag, $zip, "--title", $Title, "--notes", $body)
if ($Draft) {
  $args += "--draft"
}
if ($Prerelease) {
  $args += "--prerelease"
}

& gh @args
if ($LASTEXITCODE -ne 0) {
  if (-not $Clobber) {
    throw "Failed to create release. If the tag already exists, rerun with -Clobber to upload/replace the asset."
  }

  $uploadArgs = @("release", "upload", $Tag, $zip, "--clobber")
  & gh @uploadArgs
  if ($LASTEXITCODE -ne 0) {
    throw "Failed to upload release asset with exit code $LASTEXITCODE"
  }
}

$repoName = (& gh repo view --json nameWithOwner --jq ".nameWithOwner").Trim()
Write-Host "Release page: https://github.com/$repoName/releases/tag/$Tag"
Write-Host "Download URL: https://github.com/$repoName/releases/download/$Tag/$($zipFile.Name)"
