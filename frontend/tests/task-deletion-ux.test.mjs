import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("task deletion exposes an immediate busy state in bulk and row actions", () => {
  const appState = app.slice(app.indexOf("function App()"), app.indexOf("const deleteTasks"));
  const deletion = app.slice(app.indexOf("const deleteTasks"), app.indexOf("const createBackup"));
  const tasksPage = app.slice(app.indexOf("function TasksPage"), app.indexOf("function ActiveTaskCard"));
  const taskTable = app.slice(app.indexOf("function TaskTable"), app.indexOf("function ResultsPage"));

  assert.match(appState, /const \[deletingTaskIds, setDeletingTaskIds\]/);
  assert.match(deletion, /setDeletingTaskIds/);
  assert.ok(
    deletion.indexOf("setDeletingTaskIds") < deletion.indexOf("await api"),
    "the visible busy state must be set before the delete request starts",
  );
  assert.match(deletion, /finally[\s\S]*setDeletingTaskIds/);
  assert.match(tasksPage, /deletingTaskIds/);
  assert.match(tasksPage, /disabled=\{deletingSelectedTasks\}/);
  assert.match(tasksPage, /deletingSelectedTasks \? "\u5220\u9664\u4e2d…" : "\u5220\u9664\u6240\u9009"/);
  assert.match(taskTable, /disabled=\{deletingTaskIds\.includes\(task\.id\)\}/);
  assert.match(taskTable, /\u6b63\u5728\u5220\u9664\u4efb\u52a1/);
});

test("starting and completing a delete invalidates stale polling responses", () => {
  const deletion = app.slice(app.indexOf("const deleteTasks"), app.indexOf("const createBackup"));
  const polling = app.slice(app.indexOf("const refreshTaskState"), app.indexOf("useEffect(() => {", app.indexOf("const refreshTaskState")));

  assert.match(app, /operationEpochRef/);
  assert.match(deletion, /invalidateWorkspaceRequests\(requestEpoch\)/);
  assert.match(polling, /const requestEpoch = operations\.capture\(\)/);
  assert.match(polling, /!operations\.isCurrent\(requestEpoch\)/);
  assert.match(polling, /catch \(err\)[\s\S]*operations\.isCurrent\(requestEpoch\)[\s\S]*setError/);
});
