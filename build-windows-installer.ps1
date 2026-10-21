[CmdletBinding()]
param([string]$BuildRoot = (Join-Path $env:LOCALAPPDATA "NetworkInspectionBuild"), [switch]$Background, [switch]$SkipSidecar)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$BuildRoot = [IO.Path]::GetFullPath($BuildRoot)
if ($BuildRoot -notmatch '^[\x00-\x7F]+$') { throw 'BuildRoot must contain ASCII characters only.' }
New-Item -ItemType Directory -Force -Path $BuildRoot | Out-Null
if ($Background) {
    $log = Join-Path $BuildRoot 'installer-build.log'
    $errorLog = Join-Path $BuildRoot 'installer-build-error.log'
    $childArguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', ('"{0}"' -f $PSCommandPath), '-BuildRoot', ('"{0}"' -f $BuildRoot))
    if ($SkipSidecar) { $childArguments += '-SkipSidecar' }
    $process = Start-Process powershell.exe -WindowStyle Hidden -PassThru -ArgumentList $childArguments -RedirectStandardOutput $log -RedirectStandardError $errorLog
    Write-Output "Build process $($process.Id); logs: $log and $errorLog"
    return
}
& node (Join-Path $root 'scripts\release-tools.mjs') check
if ($LASTEXITCODE -ne 0) { throw 'Application version validation failed.' }
function Assert-WindowsGuiSubsystem([string]$Path) {
    $stream = [IO.File]::OpenRead($Path)
    $reader = [IO.BinaryReader]::new($stream)
    try {
        if ($reader.ReadUInt16() -ne 0x5A4D) { throw "Invalid executable: $Path" }
        $stream.Position = 0x3C
        $pe = $reader.ReadUInt32()
        $stream.Position = $pe
        if ($reader.ReadUInt32() -ne 0x4550) { throw "Invalid PE signature: $Path" }
        $stream.Position = $pe + 24 + 68
        if ($reader.ReadUInt16() -ne 2) { throw "Executable must use Windows GUI subsystem: $Path" }
    } finally { $reader.Dispose(); $stream.Dispose() }
}
function Assert-UpdaterArtifacts([string]$BundleDirectory, [string]$Version) {
    foreach ($extension in @('exe', 'msi')) {
        $installers = @(Get-ChildItem -LiteralPath $BundleDirectory -Recurse -File | Where-Object { $_.Extension -eq ".$extension" -and $_.Name -like "*_${Version}_*" })
        if ($installers.Count -ne 1) { throw "Expected exactly one version $Version .$extension installer." }
        $signaturePath = $installers[0].FullName + '.sig'
        if (-not (Test-Path -LiteralPath $signaturePath) -or -not ([IO.File]::ReadAllText($signaturePath)).Trim()) {
            throw "Missing or empty updater signature: $signaturePath"
        }
    }
}
function Initialize-TemporaryBuildJunction([string]$Path, [string]$Target) {
    if (Test-Path -LiteralPath $Path) {
        $existing = Get-Item -LiteralPath $Path -Force
        if ($existing.LinkType -ne "Junction") { throw "Refusing to replace a non-junction: $Path" }
        if ([IO.Path]::GetFullPath([string]$existing.Target) -ne [IO.Path]::GetFullPath($Target)) { throw "Junction points to another checkout: $Path" }
    } else { New-Item -ItemType Junction -Path $Path -Target $Target | Out-Null }
}
function New-BrandImage([string]$Path, [int]$Width, [int]$Height) {
    $bitmap = [Drawing.Bitmap]::new($Width, $Height)
    $graphics = [Drawing.Graphics]::FromImage($bitmap)
    $logo = [Drawing.Image]::FromFile((Join-Path $root 'src-tauri\icons\icon.png'))
    try {
        $graphics.Clear([Drawing.Color]::FromArgb(244, 246, 248))
        $size = [Math]::Min(96, [Math]::Min($Width - 20, $Height - 12))
        $graphics.DrawImage($logo, [int](($Width - $size) / 2), [int](($Height - $size) / 2), $size, $size)
        $bitmap.Save($Path, [Drawing.Imaging.ImageFormat]::Bmp)
    } finally { $logo.Dispose(); $graphics.Dispose(); $bitmap.Dispose() }
}
function New-NsisHeaderImage([string]$Path) { New-BrandImage $Path 150 57 }
function New-NsisSidebarImage([string]$Path) { New-BrandImage $Path 164 314 }
$assetDirectory = Join-Path $BuildRoot "tauri-assets"
New-Item -ItemType Directory -Force -Path $assetDirectory | Out-Null
Initialize-TemporaryBuildJunction (Join-Path $assetDirectory 'icons') (Join-Path $root 'src-tauri\icons')
Add-Type -AssemblyName System.Drawing
# WiX's resource glob does not follow directory junctions. Give both bundlers
# a regular file in the ASCII asset directory rather than the junction path.
$configIconPath = (Join-Path $assetDirectory 'app-icon.ico').Replace('\', '/')
Copy-Item -LiteralPath (Join-Path $assetDirectory 'icons\icon.ico') -Destination $configIconPath -Force
$configHeaderImagePath = (Join-Path $assetDirectory 'nsis-header.bmp').Replace('\', '/')
$configSidebarImagePath = (Join-Path $assetDirectory 'nsis-sidebar.bmp').Replace('\', '/')
New-NsisHeaderImage $configHeaderImagePath
New-NsisSidebarImage $configSidebarImagePath
$config = @{
    build = @{ beforeBuildCommand = '' }
    bundle = @{ createUpdaterArtifacts = $true; icon = @('icons/icon.ico'); windows = @{ nsis = @{
        installerIcon = $configIconPath
        uninstallerIcon = $configIconPath
        headerImage = $configHeaderImagePath
        sidebarImage = $configSidebarImagePath
    } } }
}
$configPath = Join-Path $BuildRoot 'tauri-build-config.json'
[System.IO.File]::WriteAllText($configPath, ($config | ConvertTo-Json -Depth 10), [System.Text.UTF8Encoding]::new($false))
if (-not $env:TAURI_SIGNING_PRIVATE_KEY) { throw 'TAURI_SIGNING_PRIVATE_KEY is required to produce signed updater artifacts.' }
$previousToolchain = $env:RUSTUP_TOOLCHAIN
$previousTargetDirectory = $env:CARGO_TARGET_DIR
try {
$env:RUSTUP_TOOLCHAIN = "stable-x86_64-pc-windows-gnu"
$env:CARGO_TARGET_DIR = Join-Path $BuildRoot 'cargo-target'
Push-Location (Join-Path $root 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    & npm.cmd run build
    if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
} finally { Pop-Location }
if ($SkipSidecar) {
    $sidecar = Get-Item -LiteralPath (Join-Path $root 'src-tauri\binaries\inspection-api-x86_64-pc-windows-gnu.exe') -ErrorAction Stop
    $sources = @(Get-ChildItem (Join-Path $root 'backend\app') -Recurse -File -Filter '*.py') + @(Get-Item (Join-Path $root 'backend\main.py'),(Join-Path $root 'backend\desktop_main.py'),(Join-Path $root 'backend\requirements-runtime.txt'),(Join-Path $root 'backend\requirements-build.txt'))
    if ($sources | Where-Object LastWriteTimeUtc -gt $sidecar.LastWriteTimeUtc) { throw 'Sidecar is older than backend source; rebuild without -SkipSidecar.' }
} else {
    & (Join-Path $root 'backend\build-sidecar.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Sidecar build failed.' }
}
Push-Location $root
try {
    & "$root\frontend\node_modules\.bin\tauri.cmd" build --config $configPath --target x86_64-pc-windows-gnu --bundles nsis,msi -- --locked
    if ($LASTEXITCODE -ne 0) { throw 'Tauri installer build failed.' }
} finally { Pop-Location }
$releaseDirectory = Join-Path $env:CARGO_TARGET_DIR 'x86_64-pc-windows-gnu\release'
Assert-WindowsGuiSubsystem (Join-Path $releaseDirectory "network-inspection.exe")
Assert-WindowsGuiSubsystem (Join-Path $releaseDirectory "inspection-api.exe")
$version = (Get-Content -LiteralPath (Join-Path $root 'src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version
Assert-UpdaterArtifacts (Join-Path $releaseDirectory 'bundle') $version
Write-Output "Installers and updater signatures: $(Join-Path $releaseDirectory 'bundle')"
} finally {
    $env:RUSTUP_TOOLCHAIN = $previousToolchain
    $env:CARGO_TARGET_DIR = $previousTargetDirectory
}
