import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("task polling cannot overlap or replace a newly selected task with stale detail", () => {
  const polling = app.slice(app.indexOf("const refreshTaskState"), app.indexOf("useEffect(() => {", app.indexOf("const refreshTaskState")));
  assert.match(polling, /taskRefreshInFlight\.current/);
  assert.match(polling, /activeTaskIdRef\.current === taskId/);
  assert.match(polling, /finally/);
});

test("successful login clears an earlier login error before entering the workspace", () => {
  const login = app.slice(app.indexOf("if (!token)"), app.indexOf("return (", app.indexOf("if (!token)")));
  assert.match(login, /setError\(null\)/);
});

test("the first successful bootstrap remains initialized after logout", () => {
  const login = app.slice(app.indexOf("return <LoginScreen"), app.indexOf("return (", app.indexOf("return <LoginScreen")));
  assert.match(login, /setAuthInitialized\(true\)/);
});

test("a successful bootstrap switches the login form to login mode before login completes", () => {
  const loginScreen = app.slice(app.indexOf("function LoginScreen"), app.indexOf("export default App"));
  assert.match(loginScreen, /const \[loginMode, setLoginMode\] = useState\(initialized\)/);
  assert.match(loginScreen, /setLoginMode\(true\)/);
  assert.match(loginScreen, /if \(!loginMode\)/);
});

test("raw output loading depends on stable ids instead of polling object identity", () => {
  const resultsPage = app.slice(app.indexOf("function ResultsPage"), app.indexOf("function AdaptersPage"));
  assert.match(resultsPage, /\[task\?\.id, selectedResult\?\.id, selectedResult\?\.raw_output_status, selectedResult\?\.raw_output, operationEpoch\.sessionGeneration, operationEpoch\.workspaceGeneration, operationEpoch\.operationGeneration\]/);
  assert.match(resultsPage, /isReadCurrent\(operationEpoch\)/);
  assert.doesNotMatch(resultsPage, /\[task, selectedResult\]/);
});

test("device and task forms prevent repeated submissions", () => {
  const deviceDialog = app.slice(app.indexOf("function DeviceDialog"), app.indexOf("function TaskDialog"));
  const taskDialog = app.slice(app.indexOf("function TaskDialog"), app.indexOf("function Field"));
  for (const dialog of [deviceDialog, taskDialog]) {
    assert.match(dialog, /const \[submitting, setSubmitting\]/);
    assert.match(dialog, /if \(submitting\) return/);
    assert.match(dialog, /finally/);
    assert.match(dialog, /disabled=\{submitting/);
  }
});

test("device and task submission errors stay inside their modal", () => {
  const deviceDialog = app.slice(app.indexOf("function DeviceDialog"), app.indexOf("function TaskDialog"));
  const taskDialog = app.slice(app.indexOf("function TaskDialog"), app.indexOf("function Field"));
  for (const dialog of [deviceDialog, taskDialog]) {
    assert.match(dialog, /const \[formError, setFormError\]/);
    assert.match(dialog, /role="alert"/);
    assert.match(dialog, /className="dialog-error"/);
    assert.match(dialog, /setFormError\(message\)/);
  }
});

test("unfinished tasks expose report export as disabled instead of failing after click", () => {
  const resultsPage = app.slice(app.indexOf("function ResultsPage"), app.indexOf("function AdaptersPage"));
  assert.match(resultsPage, /disabled=\{!canExportInspectionReport\(task\) \|\| exporting\}/);
});

test("backup history paginates at twenty rows and shows readable filenames", () => {
  const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));
  assert.match(backupPage, /const backupPageSize = 20/);
  assert.match(backupPage, /const \[backupPageIndex, setBackupPageIndex\] = useState\(0\)/);
  assert.match(backupPage, /records\.slice\(backupPageIndex \* backupPageSize, \(backupPageIndex \+ 1\) \* backupPageSize\)/);
  assert.match(backupPage, /pageRecords\.map/);
  assert.match(backupPage, /backupFileName\(record\.file_path\)/);
  assert.match(backupPage, /title=\{record\.file_path \?\? undefined\}/);
  assert.match(backupPage, /上一页/);
  assert.match(backupPage, /下一页/);
});
