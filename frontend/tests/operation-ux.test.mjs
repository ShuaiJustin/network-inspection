import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("configuration backup cannot be submitted twice while it is running", () => {
  assert.match(app, /const \[backupInProgress, setBackupInProgress\]/);
  const createBackup = app.slice(app.indexOf("const createBackup"), app.indexOf("if (authInitialized"));
  assert.match(createBackup, /if \(backupInProgressRef\.current \|\| operations\.requestsBlocked\) return/);
  assert.match(createBackup, /finally/);
  assert.match(app, /disabled=\{loadingBackups \|\| backupInProgress \|\| deletingBackupIds\.length > 0 \|\| !backupDeviceIds\.length\}/);
});

test("database backup, restore, settings save, and password controls share the exclusive busy state", () => {
  const settingsPage = app.slice(app.indexOf("function SettingsPage"), app.indexOf("function PasswordChangeDialog"));
  assert.match(settingsPage, /const \[backingUpDatabase, setBackingUpDatabase\]/);
  assert.match(settingsPage, /const writeBusy = saving \|\| restoring \|\| backingUpDatabase \|\| exclusiveBusy/);
  assert.match(settingsPage, /disabled=\{writeBusy\}/);
  assert.match(settingsPage, /PasswordChangeDialog disabled=\{exclusiveBusy\}/);
});

test("the authenticated workspace exposes a real logout action", () => {
  assert.match(app, /LogOut/);
  assert.match(app, /api<void>\("\/auth\/logout"/);
  assert.match(app, /aria-label="退出登录"/);
});

test("Excel import and result screenshot expose a busy state", () => {
  assert.match(app, /const \[importing, setImporting\]/);
  assert.match(app, /if \(importing \|\| operations\.requestsBlocked\) return/);
  assert.match(app, /disabled=\{importing\}/);
  const resultsPage = app.slice(app.indexOf("function ResultsPage"), app.indexOf("function AdaptersPage"));
  assert.match(resultsPage, /const \[capturing, setCapturing\]/);
  assert.match(resultsPage, /if \(capturing \|\| !task \|\| !selectedResult \|\| !screenshotRef\.current\) return/);
  assert.match(resultsPage, /finally/);
  const resultDrawer = app.slice(app.indexOf("function ResultDrawer"), app.indexOf("function AdaptersPage"));
  assert.match(resultDrawer, /disabled=\{capturing\}/);
  assert.match(resultDrawer, /保存截图/);
});

test("password change cannot be submitted or closed repeatedly", () => {
  const dialog = app.slice(app.indexOf("function PasswordChangeDialog"), app.indexOf("function LogsPage"));
  assert.match(dialog, /if \(submitting\) return/);
  assert.match(dialog, /disabled=\{submitting\}/);
});

test("task retry has an immediate per-row busy state", () => {
  assert.match(app, /const \[retryingTaskIds, setRetryingTaskIds\]/);
  assert.match(app, /const retryingTaskIdsRef = useRef\(new Set<number>\(\)\)/);
  const retry = app.slice(app.indexOf("const retryTask ="), app.indexOf("const deleteTasks ="));
  assert.match(retry, /retryingTaskIdsRef\.current\.has\(task\.id\)/);
  assert.match(retry, /\/tasks\/\$\{task\.id\}\/retry/);
  assert.match(retry, /finally/);
  assert.match(app, /retryingTaskIds\.includes\(task\.id\)/);
  assert.match(app, /正在重试任务|重试中/);
});

test("workspace refresh is single-flight and exposes its busy state", () => {
  assert.match(app, /const workspaceRefreshInFlightRef = useRef<Promise<void> \| null>\(null\)/);
  const refresh = app.slice(app.indexOf("const refresh ="), app.indexOf("const refreshTaskState"));
  assert.match(refresh, /if \(workspaceRefreshInFlightRef\.current\) return workspaceRefreshInFlightRef\.current/);
  assert.match(refresh, /workspaceRefreshInFlightRef\.current = refreshRequest/);
  assert.match(refresh, /workspaceRefreshInFlightRef\.current === refreshRequest/);
  assert.match(app, /disabled=\{loading\}[^>]*title=\{loading \? "正在刷新" : "刷新"\}/);
});

test("backup row actions prevent duplicate requests and show progress", () => {
  const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));
  assert.match(backupPage, /const \[comparingBackupId, setComparingBackupId\]/);
  assert.match(backupPage, /const comparingBackupIdRef = useRef<number \| null>\(null\)/);
  assert.match(backupPage, /if \(comparingBackupIdRef\.current !== null\) return/);
  assert.match(backupPage, /const \[openingBackupId, setOpeningBackupId\]/);
  assert.match(backupPage, /const openingBackupIdRef = useRef<number \| null>\(null\)/);
  assert.match(backupPage, /if \(openingBackupIdRef\.current !== null\) return/);
  assert.match(backupPage, /finally/g);
  assert.match(backupPage, /正在对比…|对比中…/);
  assert.match(backupPage, /正在打开目录|打开中…/);
});

test("backup list, comparison, and directory errors cannot clear one another", () => {
  const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));
  const compare = backupPage.slice(backupPage.indexOf("const compare ="), backupPage.indexOf("const openDirectory ="));
  const openDirectory = backupPage.slice(backupPage.indexOf("const openDirectory ="), backupPage.indexOf("const deleteBackups ="));

  assert.match(backupPage, /const \[listError, setListError\]/);
  assert.match(backupPage, /const \[compareError, setCompareError\]/);
  assert.match(backupPage, /const \[openDirectoryError, setOpenDirectoryError\]/);
  assert.doesNotMatch(compare, /setListError|setOpenDirectoryError/);
  assert.doesNotMatch(openDirectory, /setListError|setCompareError/);
  assert.match(backupPage, /compareError \? <p[^>]*role="alert"[^>]*>\{compareError\}<\/p> : diffMessage/);
});

test("only the active device sort column emits aria-sort", () => {
  const devicesPage = app.slice(app.indexOf("function DevicesPage"), app.indexOf("function TasksPage"));
  assert.match(devicesPage, /aria-sort=\{sortField === field \? \(sortDirection === "asc" \? "ascending" : "descending"\) : undefined\}/);
  assert.doesNotMatch(devicesPage, /aria-sort=\{[^}]*"none"/);
});

test("exclusive failures release, refresh, and then surface their original error", () => {
  const restore = app.slice(app.indexOf("onDatabaseRestore={async"), app.indexOf("onPasswordChange={async"));
  const password = app.slice(app.indexOf("onPasswordChange={async"), app.indexOf("/>}", app.indexOf("onPasswordChange={async")));

  for (const handler of [restore, password]) {
    const release = handler.lastIndexOf("operations.finishExclusive(exclusiveOperation)");
    const refresh = handler.indexOf("await refresh()", release);
    assert.ok(release >= 0 && refresh > release, "failed exclusive operation must refresh after release");
  }
  assert.ok(restore.indexOf("setError(userErrorMessage(failure", restore.indexOf("await refresh()")) > restore.indexOf("await refresh()"));
  assert.ok(password.indexOf("throw failure", password.indexOf("await refresh()")) > password.indexOf("await refresh()"));
});
