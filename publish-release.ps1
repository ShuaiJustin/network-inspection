[CmdletBinding()]
param(
    [string]$Repository = 'ShuaiJustin/network-inspection',
    [string]$NotesFile,
    [string]$BuildRoot = (Join-Path $env:LOCALAPPDATA 'NetworkInspectionBuild'),
    [switch]$SkipBuild,
    [switch]$Draft
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Push-Location $PSScriptRoot
try {
    $gh = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $gh -and (Test-Path 'C:\Program Files\GitHub CLI\gh.exe')) { $gh = Get-Item 'C:\Program Files\GitHub CLI\gh.exe' }
    if (-not $gh) { throw 'Install GitHub CLI and run gh auth login first.' }
    $ghPath = if ($gh -is [System.IO.FileInfo]) { $gh.FullName } else { $gh.Source }
    if ($Repository -notmatch '^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$') { throw 'Use a GitHub repository in owner/name form.' }
    & node (Join-Path $PSScriptRoot 'scripts\release-tools.mjs') check
    if ($LASTEXITCODE -ne 0) { throw 'Application versions differ.' }
    $version = (Get-Content (Join-Path $PSScriptRoot 'src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version
    if (-not $NotesFile) { $NotesFile = Join-Path $PSScriptRoot "docs\releases\$version.md" }
    if (-not (Test-Path -LiteralPath $NotesFile)) { throw 'Release notes file does not exist.' }
    & git diff --quiet HEAD --
    if ($LASTEXITCODE -ne 0) { throw 'Commit all tracked application changes before publishing.' }
    $uncommitted = @(& git status --porcelain --untracked-files=all -- .github backend frontend src-tauri scripts start-dev.ps1 build-windows-installer.ps1 publish-release.ps1 README.md .gitignore docs/releases)
    if ($uncommitted.Count -gt 0) { throw 'Commit all application source files, including untracked files, before publishing.' }
    $commit = (& git rev-parse HEAD).Trim()
    & $ghPath api "repos/$Repository/commits/$commit" --silent
    if ($LASTEXITCODE -ne 0) { throw 'Push the source commit to GitHub before publishing.' }
    # A missing release is expected here. Capture its native stderr without
    # triggering Windows PowerShell 5's terminating NativeCommandError behavior.
    $probeInfo = New-Object System.Diagnostics.ProcessStartInfo
    $probeInfo.FileName = $ghPath
    $probeInfo.Arguments = "release view v$version --repo $Repository"
    $probeInfo.UseShellExecute = $false
    $probeInfo.CreateNoWindow = $true
    $probeInfo.RedirectStandardOutput = $true
    $probeInfo.RedirectStandardError = $true
    $probe = [System.Diagnostics.Process]::Start($probeInfo)
    $probe.StandardOutput.ReadToEnd() | Out-Null
    $probe.StandardError.ReadToEnd() | Out-Null
    $probe.WaitForExit()
    $releaseExists = $probe.ExitCode -eq 0
    $probe.Dispose()
    if ($releaseExists) { throw "Release v$version already exists; increase the version instead of replacing it." }
    if (-not $SkipBuild) {
        & (Join-Path $PSScriptRoot 'build-windows-installer.ps1') -BuildRoot $BuildRoot
        if ($LASTEXITCODE -ne 0) { throw 'Installer build failed.' }
    }
    $bundle = Join-Path $BuildRoot 'cargo-target\x86_64-pc-windows-gnu\release\bundle'
    $releaseDirectory = Join-Path $PSScriptRoot "output\release-$version"
    New-Item -ItemType Directory -Force -Path $releaseDirectory | Out-Null
    if (Get-ChildItem -LiteralPath $releaseDirectory -File | Where-Object Name -NotIn 'latest.json','SHA256SUMS.txt') {
        throw 'Release staging directory already contains assets; use a fresh version/staging directory.'
    }
    foreach ($extension in @('exe','msi')) {
        $installers = @(Get-ChildItem -LiteralPath $bundle -Recurse -File | Where-Object { $_.Extension -eq ".$extension" -and $_.Name -like "*_${version}_*" })
        if ($installers.Count -ne 1) { throw "Expected one version $version .$extension installer." }
        foreach ($installer in $installers) {
            if (-not (Test-Path -LiteralPath ($installer.FullName + '.sig'))) { throw "Missing signature for $($installer.Name)" }
            Copy-Item -LiteralPath $installer.FullName,($installer.FullName + '.sig') -Destination $releaseDirectory
        }
    }
    & node (Join-Path $PSScriptRoot 'scripts\release-tools.mjs') prepare $releaseDirectory $NotesFile $Repository
    if ($LASTEXITCODE -ne 0) { throw 'Release manifest validation failed.' }
    $assets = @(Get-ChildItem -LiteralPath $releaseDirectory -File | Select-Object -ExpandProperty FullName)
    # Upload all artifacts into a draft before making latest.json discoverable.
    & $ghPath release create "v$version" @assets --repo $Repository --target $commit --title "Network Inspection v$version" --notes-file $NotesFile --draft
    if ($LASTEXITCODE -ne 0) { throw 'Release upload failed. Inspect the draft before retrying.' }
    if (-not $Draft) {
        & $ghPath release edit "v$version" --repo $Repository --draft=false --latest
        if ($LASTEXITCODE -ne 0) { throw 'Assets uploaded but publishing failed; the draft is retained.' }
    }
    & $ghPath release view "v$version" --repo $Repository
} finally { Pop-Location }
