import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const apiClient = readFileSync(new URL("../src/app/api-client.ts", import.meta.url), "utf8");

test("startup refreshes an unavailable Tauri backend config while the sidecar starts", () => {
  assert.match(apiClient, /async function currentBackendConfig/);
  assert.match(apiClient, /if \(backend\.url\) return backend/);
  assert.match(apiClient, /backendConfigPromise = invoke<BackendConfig>\("backend_config"\)/);
  assert.match(apiClient, /const backend = await currentBackendConfig\(\)/);
});

test("sidecar reconnect is single-flight and only retries startup after a successful spawn", () => {
  const appState = app.slice(app.indexOf("function App()"), app.indexOf("const refresh ="));
  const reconnect = app.slice(app.indexOf("const reconnectBackend"), app.indexOf("if (authInitialized"));
  const loadingScreen = app.slice(app.indexOf("if (authInitialized"), app.indexOf("if (!token)"));

  assert.match(appState, /const \[reconnectingBackend, setReconnectingBackend\]/);
  assert.match(appState, /const reconnectingBackendRef = useRef\(false\)/);
  assert.match(reconnect, /if \(reconnectingBackendRef\.current\) return/);
  assert.match(reconnect, /\.then\(\(\) => setBackendAttempt/);
  const finallyBlock = reconnect.slice(reconnect.indexOf(".finally"));
  assert.doesNotMatch(finallyBlock, /setBackendAttempt/);
  assert.match(loadingScreen, /disabled=\{reconnectingBackend\}/);
  assert.match(loadingScreen, /reconnectingBackend \? "\u91cd\u542f\u4e2d…" : "\u91cd\u65b0\u8fde\u63a5"/);
});

test("task cancellation and device deletion expose immediate row busy states", () => {
  const appState = app.slice(app.indexOf("function App()"), app.indexOf("const refresh ="));
  const cancel = app.slice(app.indexOf("const cancelTask"), app.indexOf("const deleteTasks"));
  const deleteDevice = app.slice(app.indexOf("const deleteDevice"), app.indexOf("const cancelTask"));
  const devicesPage = app.slice(app.indexOf("function DevicesPage"), app.indexOf("function TasksPage"));
  const activeTask = app.slice(app.indexOf("function ActiveTaskCard"), app.indexOf("function TaskTable"));
  const taskTable = app.slice(app.indexOf("function TaskTable"), app.indexOf("function ResultsPage"));

  assert.match(appState, /cancellingTaskIdsRef/);
  assert.match(appState, /deletingDeviceIdsRef/);
  assert.ok(cancel.indexOf("setCancellingTaskIds") < cancel.indexOf("await api"));
  assert.ok(deleteDevice.indexOf("setDeletingDeviceIds") < deleteDevice.indexOf("await api"));
  assert.match(devicesPage, /disabled=\{deletingDeviceIds\.includes\(device\.id\)\}/);
  assert.match(activeTask, /disabled=\{cancellingTaskIds\.includes\(task\.id\)\}/);
  assert.match(taskTable, /disabled=\{cancellingTaskIds\.includes\(task\.id\)\}/);
});

test("log searches ignore older responses and do not issue old-offset plus new-offset requests", () => {
  const logsPage = app.slice(app.indexOf("function LogsPage"), app.indexOf("function DeviceDialog"));

  assert.match(logsPage, /const logRequestIdRef = useRef\(0\)/);
  assert.match(logsPage, /const requestId = \+\+logRequestIdRef\.current/);
  assert.match(logsPage, /if \(logRequestIdRef\.current !== requestId\) return/);
  assert.match(logsPage, /const \[appliedKeyword, setAppliedKeyword\]/);
  assert.match(logsPage, /if \(offset === 0 && appliedKeyword === nextKeyword\) void loadLogs\(\)/);
  assert.match(logsPage, /setOffset\(0\);[\s\S]*setAppliedKeyword\(nextKeyword\)/);
});

test("configuration comparison serializes selections until the active request finishes", () => {
  const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));

  assert.match(backupPage, /const comparingBackupIdRef = useRef<number \| null>\(null\)/);
  assert.match(backupPage, /if \(comparingBackupIdRef\.current !== null\) return/);
  assert.match(backupPage, /finally/);
  assert.match(backupPage, /comparingBackupIdRef\.current = null/);
});
