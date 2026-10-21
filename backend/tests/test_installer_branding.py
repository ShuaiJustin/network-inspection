from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_windows_installer_uses_application_branding() -> None:
    script = (PROJECT_ROOT / "build-windows-installer.ps1").read_text(
        encoding="utf-8-sig"
    )

    assert "installerIcon = $configIconPath" in script
    assert "uninstallerIcon = $configIconPath" in script
    assert "headerImage = $configHeaderImagePath" in script
    assert "sidebarImage = $configSidebarImagePath" in script
    assert "function New-NsisHeaderImage" in script
    assert "function New-NsisSidebarImage" in script
    assert '$assetDirectory = Join-Path $BuildRoot "tauri-assets"' in script
    assert "function Initialize-TemporaryBuildJunction" in script
    assert "-ItemType Junction" in script
    assert '$existing.LinkType -ne "Junction"' in script
    assert "[System.Text.UTF8Encoding]::new($false)" in script
    assert "[System.IO.File]::WriteAllText" in script
    assert "SymbolicLink" not in script
    assert "Initialize-TemporaryBuildAsset" not in script
    assert "src-tauri\\build-icon.ico" not in script
    assert "src-tauri\\build-nsis-header.bmp" not in script
    assert "src-tauri\\build-nsis-sidebar.bmp" not in script
