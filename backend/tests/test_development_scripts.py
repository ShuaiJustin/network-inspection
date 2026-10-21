import shutil
import subprocess
import struct
import socket
import sys
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
POWERSHELL = shutil.which('powershell.exe')


@pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell unavailable')
@pytest.mark.parametrize('valid', [False, True])
def test_updater_artifacts_require_current_installer_and_signature_for_both_formats(tmp_path, valid):
    for extension in ['exe', 'msi']:
        name = f'application_{"0.1.33" if valid else "0.1.32"}_x64.{extension}'
        (tmp_path / name).write_bytes(b'installer')
        (tmp_path / (name + '.sig')).write_text('signature')
    def quote(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = (
        "$ErrorActionPreference='Stop';$t=$null;$e=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
        f"{quote(ROOT / 'build-windows-installer.ps1')},[ref]$t,[ref]$e);"
        "$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq 'Assert-UpdaterArtifacts'},$true);"
        ". ([scriptblock]::Create($fn.Extent.Text));"
        f"try{{Assert-UpdaterArtifacts {quote(tmp_path)} '0.1.33'}}catch{{Write-Output $_;exit 1}}"
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
    assert result.returncode == (0 if valid else 1), result.stdout + result.stderr


@pytest.mark.skipif(not POWERSHELL or not shutil.which('node'), reason='Windows development runtimes unavailable')
def test_child_processes_receive_token_without_mutating_parent_environment(tmp_path):
    backend = tmp_path / 'backend'
    vite = tmp_path / 'frontend/node_modules/vite/bin'
    backend.mkdir()
    vite.mkdir(parents=True)
    (backend / 'uvicorn.py').write_text(
        "import os,pathlib;pathlib.Path('backend-token.txt').write_text(os.environ['INSPECTION_RUNTIME_TOKEN'])",
        encoding='utf-8',
    )
    (vite / 'vite.js').write_text(
        "require('node:fs').writeFileSync('frontend-token.txt',process.env.VITE_INSPECTION_RUNTIME_TOKEN)",
        encoding='utf-8',
    )
    def quoted(value):
        return "'" + str(value).replace("'", "''") + "'"
    command = (
        "$ErrorActionPreference='Stop';$t=$null;$e=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
        f"{quoted(ROOT / 'start-dev.ps1')},[ref]$t,[ref]$e);"
        "$functions=$ast.FindAll({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst]},$true);"
        "foreach($fn in $functions){. ([scriptblock]::Create($fn.Extent.Text))};"
        f"$root={quoted(tmp_path)};$backend={quoted(backend)};$venvPython={quoted(sys.executable)};"
        "$env:INSPECTION_RUNTIME_TOKEN='original-api';$env:VITE_INSPECTION_RUNTIME_TOKEN='original-ui';"
        "try{$api=Start-BackendProcess 'ephemeral-test-token'}catch{throw \"Backend $venvPython in ${backend}: $_\"};"
        "try{$ui=Start-FrontendProcess 'ephemeral-test-token'}catch{throw \"Frontend in ${root}: $_\"};"
        "$api.WaitForExit();$ui.WaitForExit();"
        "if($api.ExitCode -or $ui.ExitCode){exit 1};"
        "@{api=$env:INSPECTION_RUNTIME_TOKEN;ui=$env:VITE_INSPECTION_RUNTIME_TOKEN}|ConvertTo-Json -Compress"
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(result.stdout) == {'api': 'original-api', 'ui': 'original-ui'}
    assert (backend / 'backend-token.txt').read_text() == 'ephemeral-test-token'
    assert (tmp_path / 'frontend/frontend-token.txt').read_text() == 'ephemeral-test-token'


@pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell unavailable')
def test_development_refuses_occupied_api_port_before_installing():
    with socket.socket() as listener:
        try:
            listener.bind(('127.0.0.1', 8000))
        except OSError:
            pytest.skip('Port 8000 already occupied')
        listener.listen()
        result = subprocess.run(
            [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
             str(ROOT / 'start-dev.ps1'), '-Python', 'nonexistent-python.exe'],
            capture_output=True, text=True, errors='replace', timeout=10,
        )
    assert result.returncode != 0
    assert 'Port 127.0.0.1:8000 is already in use' in result.stderr


@pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell unavailable')
def test_installer_rejects_unicode_build_root_before_starting_tools(tmp_path):
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File',
         str(ROOT / 'build-windows-installer.ps1'), '-BuildRoot', str(tmp_path / '构建')],
        capture_output=True, text=True, errors='replace',
    )
    assert result.returncode != 0
    assert 'BuildRoot must contain ASCII characters' in result.stderr
    assert not (tmp_path / '构建').exists()


@pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell unavailable')
def test_development_script_parses():
    path = str(ROOT / 'start-dev.ps1').replace("'", "''")
    result = subprocess.run(
        [POWERSHELL, '-NoProfile', '-Command',
         "$t=$null;$e=$null;[void][System.Management.Automation.Language.Parser]::ParseFile("
         f"'{path}',[ref]$t,[ref]$e);if($e.Count){{$e;exit 1}}"],
        capture_output=True, text=True, errors='replace',
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not POWERSHELL, reason='Windows PowerShell unavailable')
@pytest.mark.parametrize('subsystem, expected', [(2, 0), (3, 1)])
def test_gui_subsystem_guard_checks_pe_header(tmp_path, subsystem, expected):
    executable = tmp_path / 'application.exe'
    header = bytearray(256)
    struct.pack_into('<H', header, 0, 0x5A4D)
    struct.pack_into('<I', header, 0x3C, 64)
    struct.pack_into('<I', header, 64, 0x4550)
    struct.pack_into('<H', header, 64 + 24 + 68, subsystem)
    executable.write_bytes(header)
    script_path = str(ROOT / 'build-windows-installer.ps1').replace("'", "''")
    binary_path = str(executable).replace("'", "''")
    command = (
        "$ErrorActionPreference='Stop';$t=$null;$e=$null;"
        "$ast=[System.Management.Automation.Language.Parser]::ParseFile("
        f"'{script_path}',[ref]$t,[ref]$e);"
        "$fn=$ast.Find({param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] "
        "-and $n.Name -eq 'Assert-WindowsGuiSubsystem'},$true);"
        ". ([scriptblock]::Create($fn.Extent.Text));"
        f"try {{Assert-WindowsGuiSubsystem '{binary_path}'}} catch {{Write-Output $_;exit 1}}"
    )
    result = subprocess.run([POWERSHELL, '-NoProfile', '-Command', command], capture_output=True, text=True)
    assert result.returncode == expected, result.stdout + result.stderr
