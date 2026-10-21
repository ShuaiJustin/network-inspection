import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const taskView = await import("../src/app/task-view.js").catch(() => null);

test("task summary separates active, completed, and abnormal tasks", () => {
  assert.equal(typeof taskView?.summarizeTasks, "function");
  const summary = taskView.summarizeTasks([
    { status: "queued" },
    { status: "running" },
    { status: "completed" },
    { status: "partial_failed" },
    { status: "failed" },
    { status: "cancelled", finished_at: "2026-08-04T09:00:00" },
  ]);

  assert.deepEqual(summary, { total: 6, active: 2, completed: 1, attention: 3 });
});

test("task filters cover every visible terminal state", () => {
  assert.deepEqual(
    taskView?.TASK_FILTERS?.map((filter) => filter.id),
    ["all", "active", "completed", "partial_failed", "failed", "cancelled"],
  );
  const tasks = [
    { id: 1, status: "queued" },
    { id: 2, status: "running" },
    { id: 3, status: "completed" },
    { id: 4, status: "partial_failed" },
  ];

  assert.deepEqual(taskView.filterTasks(tasks, "active").map((task) => task.id), [1, 2]);
  assert.deepEqual(taskView.filterTasks(tasks, "completed").map((task) => task.id), [3]);
  assert.equal(taskView.filterTasks(tasks, "all").length, 4);
});

test("progress tone follows the task outcome", () => {
  assert.equal(taskView?.taskProgressTone?.("queued"), "neutral");
  assert.equal(taskView?.taskProgressTone?.("running"), "active");
  assert.equal(taskView?.taskProgressTone?.("completed"), "success");
  assert.equal(taskView?.taskProgressTone?.("partial_failed"), "warning");
  assert.equal(taskView?.taskProgressTone?.("failed"), "danger");
  assert.equal(taskView?.taskProgressTone?.("cancelled"), "neutral");
});

test("active tasks are presented separately from task history", () => {
  assert.equal(typeof taskView?.partitionVisibleTasks, "function");
  const tasks = [
    { id: 1, status: "queued" },
    { id: 2, status: "running" },
    { id: 3, status: "completed" },
    { id: 4, status: "failed" },
  ];

  assert.deepEqual(taskView.partitionVisibleTasks(tasks), {
    active: tasks.slice(0, 2),
    history: tasks.slice(2),
  });
});

test("a cancelled task stays active until its worker records completion", () => {
  const cancelling = { id: 1, status: "cancelled", finished_at: null };
  const cancelled = { id: 2, status: "cancelled", finished_at: "2026-08-04T09:00:00" };

  assert.deepEqual(taskView.filterTasks([cancelling, cancelled], "active"), [cancelling]);
  assert.deepEqual(taskView.partitionVisibleTasks([cancelling, cancelled]), {
    active: [cancelling],
    history: [cancelled],
  });
  assert.deepEqual(taskView.summarizeTasks([cancelling, cancelled]), {
    total: 2,
    active: 1,
    completed: 0,
    attention: 1,
  });

  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  assert.match(app, /正在取消/);
});

test("task page uses the Figma-style summary, filters, table, and status progress", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

  for (const marker of ["task-summary", "task-filters", "task-table", "task-progress__fill"]) {
    assert.match(app, new RegExp(marker), `missing ${marker} task view structure`);
  }
  assert.match(app, /tasks\.some\(.*queued.*running/s, "the task list must refresh while a task is active");
  assert.match(css, /\.task-summary\s*\{[^}]*grid-template-columns:\s*repeat\(4,/s);
  assert.match(css, /\.task-progress\s*\{[^}]*height:\s*6px/s);
  assert.match(css, /\.task-progress__fill\s*\{[^}]*transition:/s);
  assert.match(css, /\.task-progress__fill--active\s*\{[^}]*var\(--c-accent\)/s);
  assert.match(css, /\.task-progress__fill--success\s*\{[^}]*var\(--c-ok\)/s);
  assert.match(css, /\.task-progress__fill--warning\s*\{[^}]*var\(--c-warn\)/s);
  assert.match(css, /\.task-progress__fill--danger\s*\{[^}]*var\(--c-danger\)/s);
});

test("task actions remain visible at the minimum desktop window width", () => {
  const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

  assert.match(css, /@media\s*\(max-width:\s*1180px\)[\s\S]*?\.task-table\s*\{[^}]*min-width:\s*780px/s);
  assert.match(css, /@media\s*\(max-width:\s*1180px\)[\s\S]*?\.progress-cell\s*\{[^}]*min-width:\s*0/s);
  assert.match(css, /\.task-actions-column\s*\{[^}]*white-space:\s*nowrap/s);
});

test("new and running tasks use the screenshot-two progress experience", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
  const createdHandler = app.match(/onCreated=\{async \(task, requestEpoch\) => \{([\s\S]*?)\}\} \/>/);

  assert.ok(createdHandler, "missing task-created handler");
  assert.match(createdHandler[1], /navigateToPage\("tasks"\)/, "new tasks must open the progress page");
  assert.doesNotMatch(createdHandler[1], /selectTask\(task\)/, "new tasks must not open an empty result page");
  for (const marker of ["active-task-list", "active-task-card", "active-task-progress", "active-task-progress__fill"]) {
    assert.match(app, new RegExp(marker), `missing ${marker}`);
  }
  assert.match(css, /\.active-task-progress\s*\{[^}]*width:\s*100%[^}]*height:\s*6px/s);
  assert.match(css, /\.active-task-progress__fill\s*\{[^}]*transition:/s);
});
