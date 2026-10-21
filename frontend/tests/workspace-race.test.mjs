import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("stored-token startup verifies authentication before loading workspace data", () => {
  const startup = app.slice(app.indexOf("waitForBackend("), app.indexOf("}, [backendAttempt])"));
  const refreshEffectStart = app.indexOf("useEffect(() => { if (!authInitialized");
  const refreshEffect = app.slice(refreshEffectStart, refreshEffectStart + 180);

  assert.match(startup, /resetSessionOnUnauthorized:\s*false/);
  assert.match(startup, /operationEpochRef\.current\.(?:beginSession|endSession)\(\)/);
  assert.match(refreshEffect, /if \(!authInitialized \|\| !token\) return/);
});

test("workspace refreshes ignore responses from a previous operation epoch", () => {
  const refresh = app.slice(app.indexOf("const refresh ="), app.indexOf("const refreshTaskState"));
  const logout = app.slice(app.indexOf("const logout = async"), app.indexOf("const reconnectBackend"));

  assert.match(refresh, /const requestEpoch = operations\.capture\(\)/);
  assert.match(refresh, /operations\.isCurrent\(requestEpoch\)/);
  assert.match(logout, /operationEpochRef\.current\.endSession\(\)/);
  assert.ok(
    logout.indexOf("endSession()") < logout.indexOf("await logoutRequest"),
    "logout must invalidate old requests before waiting for the service",
  );
});

test("database restore establishes and releases an exclusive workspace boundary", () => {
  const start = app.indexOf("onDatabaseRestore={async");
  const end = app.indexOf("onPasswordChange={async", start);
  const restore = app.slice(start, end);

  assert.ok(restore.indexOf("operations.beginExclusive()") < restore.indexOf("await api"));
  assert.match(restore, /operations\.isExclusiveCurrent\(exclusiveOperation\)/);
  assert.match(restore, /finally[\s\S]*operations\.finishExclusive\(exclusiveOperation\)/);
  assert.match(restore, /finishExclusive\(exclusiveOperation, \{ endSession: true \}\)/);
});

test("password change and new login establish matching session boundaries", () => {
  const passwordStart = app.indexOf("onPasswordChange={async");
  const passwordEnd = app.indexOf("/>}", passwordStart);
  const passwordChange = app.slice(passwordStart, passwordEnd);
  const loginStart = app.indexOf("return <LoginScreen");
  const loginEnd = app.indexOf("return (", loginStart);
  const login = app.slice(loginStart, loginEnd);

  assert.ok(passwordChange.indexOf("operations.beginExclusive()") < passwordChange.indexOf("await api"));
  assert.match(passwordChange, /finally[\s\S]*operations\.finishExclusive\(exclusiveOperation\)/);
  assert.match(login, /operationEpochRef\.current\.beginSession\(\)/);
  assert.ok(login.indexOf("beginSession()") < login.indexOf("setToken(value)"));
});

test("full refresh and polling share a latest-request-wins tracker", () => {
  const refresh = app.slice(app.indexOf("const refresh ="), app.indexOf("const refreshTaskState"));
  const polling = app.slice(app.indexOf("const refreshTaskState"), app.indexOf("useEffect(() => {", app.indexOf("const refreshTaskState")));

  for (const request of [refresh, polling]) {
    assert.match(request, /taskDataRequestsRef\.current\.begin\(\)/);
    assert.match(request, /taskDataRequestsRef\.current\.isLatest\(taskRequestId\)/);
  }
  assert.match(refresh, /workspaceRefreshInFlightRef\.current/);
  assert.match(polling, /taskRefreshInFlight\.current/);
});

test("leaving results intent prevents a late task detail from navigating back", () => {
  const selection = app.slice(app.indexOf("const selectTask ="), app.indexOf("const toggleDevice"));

  assert.match(selection, /const taskNavigationIntentRef = useRef\(0\)|taskNavigationIntentRef\.current/);
  assert.match(selection, /const intentId = \+\+taskNavigationIntentRef\.current/);
  assert.match(selection, /isTaskNavigationCurrent\(taskNavigationIntentRef\.current, intentId, activeTaskIdRef\.current, taskId\)/);
  assert.match(selection, /if \(navigateToResults\) setPage\("results"\)/);
});

test("successful settings save invalidates an older settings response", () => {
  const start = app.indexOf("onSave={async");
  const end = app.indexOf("onDatabaseBackup={async", start);
  const save = app.slice(start, end);

  assert.ok(save.indexOf("await api<InspectionSettings>") < save.indexOf("invalidateWorkspaceRequests(requestEpoch)"));
  assert.ok(save.indexOf("invalidateWorkspaceRequests(requestEpoch)") < save.indexOf("setSettings(saved)"));
});

test("successful writes invalidate an older single-flight refresh before reloading", () => {
  const importExcel = app.slice(app.indexOf("const importExcel"), app.indexOf("const deleteDevice"));
  const deviceSaved = app.slice(app.indexOf("onSaved={async"), app.indexOf("/>}", app.indexOf("onSaved={async")));
  const taskCreated = app.slice(app.indexOf("onCreated={async"), app.indexOf("/>}", app.indexOf("onCreated={async")));

  for (const writeRefresh of [importExcel, deviceSaved, taskCreated]) {
    assert.match(writeRefresh, /invalidateWorkspaceRequests\(requestEpoch\)/);
    assert.ok(
      writeRefresh.lastIndexOf("invalidateWorkspaceRequests(requestEpoch)") < writeRefresh.lastIndexOf("await refresh()"),
      "the completed write must invalidate an older refresh before reloading",
    );
  }
});

test("logout clears notices and every parent-level long-request busy state", () => {
  const clear = app.slice(app.indexOf("const clearAuthenticatedState"), app.indexOf("const logout = async"));

  for (const reset of [
    "setNotice(null)",
    "setError(null)",
    "setTestingConnections(new Set())",
    "setBackupInProgress(false)",
    "setExportingReport(false)",
    "setImporting(false)",
    "setLoading(false)",
  ]) {
    assert.match(clear, new RegExp(reset.replace(/[()]/g, "\\$&")));
  }
});

test("backup history loading is latest-wins and blocks backup during initial load", () => {
  const backup = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));

  assert.match(backup, /const backupRequestIdRef = useRef\(0\)/);
  assert.match(backup, /const requestId = \+\+backupRequestIdRef\.current/);
  assert.match(backup, /backupRequestIdRef\.current !== requestId/);
  assert.match(backup, /backupRequestIdRef\.current \+= 1[\s\S]*await onBackup\(backupDeviceIds\)[\s\S]*await loadBackups\(\)/);
  assert.match(backup, /disabled=\{loadingBackups \|\| backupInProgress \|\| deletingBackupIds\.length > 0 \|\| !backupDeviceIds\.length\}/);
});

test("background reads use the strict epoch while explicit navigation and writes use the operation boundary", () => {
  const refresh = app.slice(app.indexOf("const refresh ="), app.indexOf("const refreshTaskState"));
  const selection = app.slice(app.indexOf("const selectTask ="), app.indexOf("const navigateToPage"));
  assert.match(refresh, /operations\.isCurrent\(requestEpoch\)/);
  assert.match(selection, /operations\.isOperationCurrent\(requestEpoch\)/);

  const writeRanges = [
    ["const testConnection =", "const importExcel ="],
    ["const importExcel =", "const deleteDevice ="],
    ["const deleteDevice =", "const cancelTask ="],
    ["const cancelTask =", "const retryTask ="],
    ["const retryTask =", "const deleteTasks ="],
    ["const deleteTasks =", "const createBackup ="],
    ["const createBackup =", "const clearAuthenticatedState"],
  ];
  for (const [start, end] of writeRanges) {
    const handler = app.slice(app.indexOf(start), app.indexOf(end, app.indexOf(start)));
    assert.match(handler, /isOperationCurrent\(requestEpoch\)/, `${start} must use operation validity`);
  }
});

test("settings parent handlers reject writes after requests become blocked", () => {
  const settings = app.slice(app.indexOf("page === \"settings\""), app.indexOf("page === \"logs\""));
  const save = settings.slice(settings.indexOf("onSave={async"), settings.indexOf("onDatabaseBackup={async"));
  const backup = settings.slice(settings.indexOf("onDatabaseBackup={async"), settings.indexOf("onDatabaseRestore={async"));
  assert.match(save, /operations\.requestsBlocked/);
  assert.match(backup, /operations\.requestsBlocked/);
});


test("destructive exclusive operations reject in-flight parent operations", () => {
  const settings = app.slice(app.indexOf("page === \"settings\""), app.indexOf("page === \"logs\""));
  const restore = settings.slice(settings.indexOf("onDatabaseRestore={async"), settings.indexOf("onPasswordChange={async"));
  const password = settings.slice(settings.indexOf("onPasswordChange={async"));

  assert.ok(restore.indexOf("hasPendingParentOperation()") < restore.indexOf("operations.beginExclusive()"));
  assert.ok(password.indexOf("hasPendingParentOperation()") < password.indexOf("operations.beginExclusive()"));
  assert.match(app, /importingRef\.current = true/);
  assert.match(app, /importingRef\.current = false/);
});
