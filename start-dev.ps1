[CmdletBinding()]
param([string]$Python)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$portProbe = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 8000)
try { $portProbe.Start() } catch { throw 'Port 127.0.0.1:8000 is already in use. Stop that service or use the desktop application.' } finally { $portProbe.Stop() }
if (-not $Python) {
    $localPython = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe'
    if (Test-Path -LiteralPath $localPython) { $Python = $localPython }
    else { $Python = (Get-Command python -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source }
}
$backend = Join-Path $root 'backend'
$venv = Join-Path $backend '.venv-dev'
$venvPython = Join-Path $venv 'Scripts\python.exe'
function Start-BackendProcess([string]$RuntimeToken) {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $venvPython
    $startInfo.Arguments = '-m uvicorn main:app --host 127.0.0.1 --port 8000'
    $startInfo.WorkingDirectory = $backend
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables["INSPECTION_RUNTIME_TOKEN"] = $RuntimeToken
    return [Diagnostics.Process]::Start($startInfo)
}
function Start-FrontendProcess([string]$RuntimeToken) {
    $startInfo = [Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = (Get-Command node -CommandType Application -ErrorAction Stop | Select-Object -First 1).Source
    $startInfo.Arguments = 'node_modules/vite/bin/vite.js --host 127.0.0.1 --port 5173 --strictPort'
    $startInfo.WorkingDirectory = Join-Path $root 'frontend'
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.EnvironmentVariables["VITE_INSPECTION_RUNTIME_TOKEN"] = $RuntimeToken
    return [Diagnostics.Process]::Start($startInfo)
}
if (-not (Test-Path -LiteralPath $venvPython)) {
    & $Python -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the development environment.' }
}
& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $backend 'requirements-dev.txt')
if ($LASTEXITCODE -ne 0) { throw 'Could not install development dependencies.' }
Push-Location (Join-Path $root 'frontend')
try {
    & npm.cmd ci
    if ($LASTEXITCODE -ne 0) { throw 'npm ci failed.' }
    $tokenBytes = New-Object byte[] 32
    $generator = [Security.Cryptography.RandomNumberGenerator]::Create()
    try { $generator.GetBytes($tokenBytes) } finally { $generator.Dispose() }
    $runtimeToken = [Convert]::ToBase64String($tokenBytes)
    [Array]::Clear($tokenBytes, 0, $tokenBytes.Length)
    $backendProcess = $null
    $frontendProcess = $null
    try {
        $backendProcess = Start-BackendProcess $runtimeToken
        $deadline = [DateTime]::UtcNow.AddSeconds(30)
        $ready = $false
        do {
            if ($backendProcess.HasExited) { throw 'Development API exited before startup.' }
            try {
                $response = Invoke-WebRequest 'http://127.0.0.1:8000/health' -Headers @{ 'X-Inspection-Run-Token' = $runtimeToken } -UseBasicParsing -TimeoutSec 2
                $ready = $response.StatusCode -eq 200 -and ($response.Content | ConvertFrom-Json).status -eq 'ok'
            } catch { Start-Sleep -Milliseconds 250 }
        } while (-not $ready -and [DateTime]::UtcNow -lt $deadline)
        if (-not $ready -or $backendProcess.HasExited) { throw 'Development API did not become healthy.' }
        Write-Output 'API: http://127.0.0.1:8000; frontend: http://127.0.0.1:5173. Ctrl+C stops development.'
        $frontendProcess = Start-FrontendProcess $runtimeToken
        while (-not $frontendProcess.WaitForExit(500)) {
            if ($backendProcess.HasExited) { throw 'Development API exited unexpectedly.' }
        }
        if ($frontendProcess.ExitCode -ne 0) { throw 'Vite exited with an error.' }
    } finally {
        foreach ($ownedProcess in @($frontendProcess, $backendProcess)) {
            if ($ownedProcess) {
                if (-not $ownedProcess.HasExited) { Stop-Process -Id $ownedProcess.Id }
                $ownedProcess.Dispose()
            }
        }
        $runtimeToken = $null
    }
} finally { Pop-Location }
