# Network Device Inspection

Windows single-user network inspection software. The user interface uses React/Vite and the local inspection service uses FastAPI/Python. Device credentials are protected with Windows DPAPI.

## Development start

Run in PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\start-dev.ps1
```

The first visit asks for a local administrator account. Use that account for later logins.

## Implemented scope

- Local administrator initialization, login, and five failed-attempt lockout
- Device management for Huawei, H3C, Ruijie, and Cisco with SNMPv2c/v3 and SSH
- Windows DPAPI encryption; secret values are never returned by the API
- SNMP-first with SSH fallback, real protocol calls, and accurate failure classification
- Quick and full tasks, history, progress, and detailed results
- Finished tasks can be retried with their original mode and currently active
  devices while using the latest saved settings
- Excel device template/import and Excel inspection report export
- SSH configuration backup and comparison to the previous valid version
- Result screen PNG snapshot download

## Reliability and operator behavior

- Device edits, host-key trust, inspection, connection tests, and configuration
  backup share the same per-device operation lock. Idle lock entries are removed
  after the last holder or waiter exits.
- A collector failure is isolated to its device during a backup batch. Remaining
  devices continue, and every attempted device receives a history record.
- Report and screenshot filenames are reserved atomically before writing, so
  concurrent exports cannot overwrite one another.
- Workspace refresh is single-flight. Task summary polling continues while work
  is active, but full task details are fetched only while the results page is
  visible or when the operator opens it.
- Backup history shows 20 records per page, uses readable filenames, and disables
  repeated compare or open-directory actions while a request is in progress.

## Backup behavior

- Successful database restores keep the latest ten pre-restore safety snapshots
  in the application's `database_backups` directory. A locked old snapshot is
  retained and cleanup is retried after a later successful restore.
- Configuration backups retain their original root directory, so changing the
  destination does not disable existing comparisons or folder access.
- Existing database files are migrated with a safety copy first. The immediately
  preceding database schema is also accepted by restore and upgraded.
- Legacy backups are assigned a root only when their paths are inside the
  previously configured directory. Paths already outside that directory remain
  inaccessible instead of being trusted automatically.

## Packaging

Run the Windows installer build from PowerShell:

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.network-inspection\updater.key"
.\build-windows-installer.ps1 -Background
```

The latest generated artifacts are stored under:

```text
%LOCALAPPDATA%\NetworkInspectionBuild\cargo-target\x86_64-pc-windows-gnu\release\bundle
```

The desktop application selects an available `127.0.0.1` port for its bundled API service. Development mode continues to use `127.0.0.1:8000` by default.

## Project structure

```text
frontend/                 React/Vite application, browser tests and lockfile
backend/app/              FastAPI routes, models and application services
backend/tests/            Backend tests and sanitized protocol fixtures
src-tauri/                Rust desktop shell, capabilities and installer config
scripts/release-tools.mjs Version management and signed release manifest helper
docs/releases/            Release notes, one file per version
.github/workflows/        Manual GitHub Actions release workflow
start-dev.ps1             Start local development API and frontend
build-windows-installer.ps1 Build signed Windows EXE/MSI installers
publish-release.ps1       Upload artifacts as a draft, then publish
```

The frontend/backend/desktop split is intentional. Runtime data, dependencies,
build output and local design references are excluded from Git. The large
`frontend/src/app/App.tsx` can be split by page in future maintenance; that is
not required to build or publish this application.

## Development and validation

Prerequisites: Windows, Node.js 22+, Python 3.11, Rust stable GNU Windows
toolchain and MinGW GCC, NSIS and WiX for Windows installers. Python scripts
prefer a local Python311 installation or the `python` executable on PATH;
`start-dev.ps1 -Python C:\path\python.exe` selects an explicit interpreter.
Development startup creates `backend/.venv-dev`, installs pinned dependencies,
verifies port 8000 is free and waits for its API to become healthy. Ctrl+C stops
its owned API process. Packaged application data remains in its existing
Windows application data directory.

```powershell
node scripts/release-tools.mjs check
npm --prefix frontend ci
npm --prefix frontend test
npm --prefix frontend run build
$env:PYTHONPATH = 'backend'
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
cargo +stable-x86_64-pc-windows-gnu test --manifest-path src-tauri/Cargo.toml --target x86_64-pc-windows-gnu --locked
```

## Versions and GitHub Releases

Current application version: **0.1.33**. Set the next stable version with:

```powershell
node scripts/release-tools.mjs set 0.1.34
node scripts/release-tools.mjs check
```

This synchronizes Tauri, Cargo, Cargo.lock, frontend manifests and the Python
API version. Windows MSI limits apply: major/minor up to 255 and patch up to
65535. Add `docs/releases/0.1.34.md`, test, commit and push the source before
publishing. Do not replace an existing version's assets.

The updater checks the latest stable release at
`https://github.com/ShuaiJustin/network-inspection/releases/latest/download/latest.json`.
After login, it checks in the background. System settings also provides a
manual check. Downloads are signature-verified before installation. Installing
requires explicit confirmation and an idle backend; active work postpones it.
The application stops its managed API service and opens the installer, then
exits. A network or signature error leaves the current application installed.

The manifest includes separate NSIS and MSI entries so updates keep the
original installer format, plus a generic NSIS entry for compatibility.
Keep using the same installer format on a given machine. Version 0.1.32 has
no updater and must be upgraded manually once. A real in-app upgrade can only
be exercised when a later, higher signed version is published.

### Signing key

The private key is stored outside this repository at
`%USERPROFILE%\.network-inspection\updater.key`; keep a secure backup. Never
commit it or put a GitHub access token in the application. The corresponding
public key is embedded in `src-tauri/tauri.conf.json`. Losing the private key
prevents existing installations from accepting future updates.

On a new maintainer machine, restore that same private key. Only for a new
application identity with no existing installations, generate a new pair with
`frontend\node_modules\.bin\tauri.cmd signer generate --ci -w C:\secure\updater.key`
and update the public key before the first release.

### Publish locally

Install GitHub CLI and sign in with `gh auth login`, then:

```powershell
$env:TAURI_SIGNING_PRIVATE_KEY = "$env:USERPROFILE\.network-inspection\updater.key"
$env:TAURI_SIGNING_PRIVATE_KEY_PASSWORD = ''
.\publish-release.ps1 -NotesFile docs/releases/0.1.33.md
```

The publisher builds locked Windows installers and places EXE/MSI, signatures,
`latest.json` and `SHA256SUMS.txt` in `output/release-VERSION`. It uploads all
assets to a draft first, then marks the release as latest; `-Draft` leaves it
for review. `-SkipBuild` reuses a completed local build. Existing staging assets
and existing version tags/releases cause a safe stop rather than replacement.

### Publish with GitHub Actions

Repository Actions secrets: `TAURI_SIGNING_PRIVATE_KEY` contains the private key
contents, and `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` contains its password (empty
for an unencrypted key). Run **Actions → Windows release → Run workflow** from
the tested main branch. The default leaves a draft; select publish to expose
it as the latest stable release. The workflow installs pinned Python runtime
dependencies, tests the source and builds both signed installers. The GitHub
token is supplied by Actions and is never packaged in the client.
