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
.\build-windows-installer.ps1 -Background -RecoverStaleDrive
```

The latest generated artifacts are stored under:

```text
%LOCALAPPDATA%\NetworkInspectionBuild\cargo-target\x86_64-pc-windows-gnu\release\bundle
```

The desktop application selects an available `127.0.0.1` port for its bundled API service. Development mode continues to use `127.0.0.1:8000` by default.
