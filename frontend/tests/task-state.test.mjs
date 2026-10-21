import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { activeTaskAfterDeletion, canDeleteInspectionTask, canExportInspectionReport, canRetryInspectionTask, deletableTaskIds, describeTaskDeletion, resultTaskForNavigation, taskNeedsPolling } from "../src/app/task-state.js";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("only finished terminal tasks allow inspection report export", () => {
  const finishedAt = "2026-08-04T10:00:00";
  assert.equal(canExportInspectionReport({ status: "completed", finished_at: finishedAt }), true);
  assert.equal(canExportInspectionReport({ status: "partial_failed", finished_at: finishedAt }), true);
  assert.equal(canExportInspectionReport({ status: "failed", finished_at: finishedAt }), true);
  assert.equal(canExportInspectionReport({ status: "cancelled", finished_at: finishedAt }), true);
  assert.equal(canExportInspectionReport({ status: "cancelled", finished_at: null }), false);
  assert.equal(canExportInspectionReport({ status: "queued", finished_at: null }), false);
  assert.equal(canExportInspectionReport({ status: "running", finished_at: null }), false);
});

test("only a finished task may be deleted", () => {
  // Deleting mid-run would leave the worker writing results for a task that no
  // longer exists, so the button must not even be offered; the backend rejects
  // it with 409 either way.
  assert.equal(canDeleteInspectionTask({ status: "completed", finished_at: "2026-08-04T10:00:00" }), true);
  assert.equal(canDeleteInspectionTask({ status: "partial_failed", finished_at: "2026-08-04T10:00:00" }), true);
  assert.equal(canDeleteInspectionTask({ status: "failed", finished_at: "2026-08-04T10:00:00" }), true);
  assert.equal(canDeleteInspectionTask({ status: "cancelled", finished_at: "2026-08-04T10:00:00" }), true);
  assert.equal(canDeleteInspectionTask({ status: "cancelled", finished_at: null }), false);
  assert.equal(canDeleteInspectionTask({ status: "queued", finished_at: null }), false);
  assert.equal(canDeleteInspectionTask({ status: "running", finished_at: null }), false);
});

test("only a finished task may be retried", () => {
  assert.equal(canRetryInspectionTask({ status: "completed", finished_at: "2026-09-08T10:00:00" }), true);
  assert.equal(canRetryInspectionTask({ status: "partial_failed", finished_at: "2026-09-08T10:00:00" }), true);
  assert.equal(canRetryInspectionTask({ status: "failed", finished_at: "2026-09-08T10:00:00" }), true);
  assert.equal(canRetryInspectionTask({ status: "cancelled", finished_at: "2026-09-08T10:00:00" }), true);
  assert.equal(canRetryInspectionTask({ status: "cancelled", finished_at: null }), false);
  assert.equal(canRetryInspectionTask({ status: "running", finished_at: null }), false);
});

test("a batch delete only offers the tasks that can actually go", () => {
  // Selecting "all" while one task is still running must not send that id and
  // then report a partial failure the operator did not cause.
  const tasks = [
    { id: 1, status: "completed", finished_at: "2026-08-04T10:00:00" },
    { id: 2, status: "running" },
    { id: 3, status: "cancelled", finished_at: "2026-08-04T10:00:00" },
  ];
  assert.deepEqual(deletableTaskIds(tasks), [1, 3]);
  assert.deepEqual(deletableTaskIds([{ id: 9, status: "queued" }]), []);
});

test("the delete confirmation names what is about to be lost", () => {
  // History cannot be recovered, so the prompt has to state the scope rather
  // than ask a bare "are you sure".
  const single = describeTaskDeletion([{ id: 1, name: "巡检_2026/8/4", status: "completed" }]);
  assert.match(single, /巡检_2026\/8\/4/);
  assert.match(single, /无法恢复|不可恢复/);

  const many = describeTaskDeletion([
    { id: 1, name: "A", status: "completed" },
    { id: 2, name: "B", status: "failed" },
  ]);
  assert.match(many, /2/);
  // Reports and screenshots already exported to disk are deliberately kept.
  assert.match(many, /报告|截图/);
});

test("deleting the open task prevents refresh from requesting it again", () => {
  const open = { id: 7, status: "completed" };
  assert.equal(activeTaskAfterDeletion(open, [{ id: 7 }]), null);
  assert.equal(activeTaskAfterDeletion(open, [{ id: 8 }]), open);

  const deletion = app.slice(app.indexOf("const deleteTasks ="), app.indexOf("const createBackup ="));
  assert.match(deletion, /activeTaskAfterDeletion\(/);
  assert.match(deletion, /await refresh\(taskToRefresh\)/);
  assert.match(deletion, /isTaskNavigationCurrent\(taskNavigationIntentRef\.current, navigationIntentId, activeTaskIdRef\.current, expectedActiveTaskId\)/);
});

test("a cancellation request keeps polling until the worker records completion", () => {
  assert.equal(taskNeedsPolling({ status: "queued", finished_at: null }), true);
  assert.equal(taskNeedsPolling({ status: "running", finished_at: null }), true);
  assert.equal(taskNeedsPolling({ status: "cancelled", finished_at: null }), true);
  assert.equal(taskNeedsPolling({ status: "cancelled", finished_at: "2026-08-04T10:00:00" }), false);
  assert.equal(taskNeedsPolling({ status: "completed", finished_at: "2026-08-04T10:00:00" }), false);

  assert.match(app, /tasks\.some\(taskNeedsPolling\)/);
});

test("opening results selects the newest task that has persisted results", () => {
  const tasks = [
    { id: 8, completed_devices: 0, status: "running" },
    { id: 7, completed_devices: 2, status: "completed" },
    { id: 6, completed_devices: 1, status: "partial_failed" },
  ];

  assert.equal(resultTaskForNavigation(tasks, null)?.id, 7);
  assert.equal(resultTaskForNavigation(tasks, tasks[2])?.id, 6, "an explicit selection must be preserved");
  assert.equal(resultTaskForNavigation([{ id: 9, completed_devices: 0 }], null), null);
});

test("the results navigation invokes automatic task selection", () => {
  assert.match(app, /const navigateToPage\s*=.*resultTaskForNavigation/s);
  assert.match(app, /onClick=\{\(\) => navigateToPage\(id\)\}/);
});
