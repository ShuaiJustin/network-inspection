import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("active-task polling refreshes task state without toggling whole-app loading", () => {
  const polling = app.slice(app.indexOf("const refreshTaskState ="), app.indexOf("const filteredDevices ="));
  assert.match(polling, /api<Task\[]>\("\/tasks"\)/);
  assert.doesNotMatch(polling, /api<Device\[]>|api<InspectionSettings>|api<DeviceCatalog>/);
  assert.doesNotMatch(polling, /setLoading\(/);
  assert.match(app, /setInterval\(\(\) => \{ void refreshTaskState\(\); \}, 1200\)/);
});

test("active-task polling fetches full detail only while results are visible", () => {
  const polling = app.slice(app.indexOf("const refreshTaskState ="), app.indexOf("const filteredDevices ="));
  assert.match(polling, /const taskId = page === "results" \? activeTaskIdRef\.current : null/);
  assert.match(polling, /if \(taskId !== null\)/);
  assert.match(app, /\[token, tasks, activeTask, page\]/);
});

test("opening results always requests fresh task detail", () => {
  const navigation = app.slice(app.indexOf("const navigateToPage ="), app.indexOf("useEffect(() => {", app.indexOf("const navigateToPage =")));
  assert.match(navigation, /if \(taskForResults\) \{\s*void selectTask\(taskForResults, true, intentId\)/s);
  assert.doesNotMatch(navigation, /taskForResults\.id !== activeTask\?\.id/);
});

test("a stale hidden task detail cannot keep summary polling alive", () => {
  const pollingEffect = app.slice(app.indexOf("const hasActiveTask ="), app.indexOf("const filteredDevices ="));
  assert.match(pollingEffect, /page === "results"\s*&& Boolean\(activeTask && taskNeedsPolling\(activeTask\)\)/s);
});
