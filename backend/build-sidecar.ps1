[CmdletBinding()]
param([string]$Python)
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Python) {
    $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
    else { $Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
}
$bootstrapPython = (Get-Command $Python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
$msvcTarget = "x86_64-pc-windows-msvc"
$gnuTarget = "x86_64-pc-windows-gnu"
$output = Join-Path $root "..\src-tauri\binaries"
$work = Join-Path $root ".pyinstaller"
$venv = Join-Path $work "venv"
$venvPython = Join-Path $venv "Scripts\python.exe"
$pyinstallerWork = Join-Path $work "work"
$pyinstallerSpec = Join-Path $work "spec"
$buildRequirements = Join-Path $root "requirements-build.txt"

if (-not (Test-Path -LiteralPath $bootstrapPython)) {
    throw "Python 3.11 was not found at $bootstrapPython"
}
if (-not (Test-Path -LiteralPath $buildRequirements)) {
    throw "Build requirements were not found at $buildRequirements"
}

New-Item -ItemType Directory -Force -Path $output, $work | Out-Null
& $bootstrapPython -m venv --clear $venv
if ($LASTEXITCODE -ne 0) {
    throw "Could not create the isolated sidecar build environment."
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw "The isolated sidecar Python executable was not created at $venvPython"
}

& $venvPython -m pip install --disable-pip-version-check -r $buildRequirements
if ($LASTEXITCODE -ne 0) {
    throw "Could not install the pinned sidecar build dependencies."
}

& $venvPython -m PyInstaller --noconfirm --clean --onefile --noconsole --name "inspection-api-$msvcTarget" --distpath $output --workpath $pyinstallerWork --specpath $pyinstallerSpec (Join-Path $root "desktop_main.py")
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed to build the sidecar."
}

$msvcSidecar = Join-Path $output "inspection-api-$msvcTarget.exe"
$gnuSidecar = Join-Path $output "inspection-api-$gnuTarget.exe"
if (-not (Test-Path -LiteralPath $msvcSidecar)) {
    throw "PyInstaller did not produce $msvcSidecar"
}

# The packaged Python executable is a native Windows sidecar, independent of Cargo's ABI target.
Copy-Item -LiteralPath $msvcSidecar -Destination $gnuSidecar -Force
