const terminalTaskStatuses = new Set([
  "completed",
  "partial_failed",
  "failed",
  "cancelled",
]);

export function canExportInspectionReport(task) {
  return terminalTaskStatuses.has(task.status) && Boolean(task.finished_at);
}

export function canRetryInspectionTask(task) {
  return terminalTaskStatuses.has(task.status) && Boolean(task.finished_at);
}

// A task that is still queued or running has a worker writing results into it,
// so it must be cancelled before it can go. The backend enforces this too; the
// UI simply does not offer an action it knows would be refused.
export function canDeleteInspectionTask(task) {
  return terminalTaskStatuses.has(task.status) && Boolean(task.finished_at);
}

export function taskNeedsPolling(task) {
  return ["queued", "running"].includes(task.status)
    || (task.status === "cancelled" && !task.finished_at);
}

export function deletableTaskIds(tasks) {
  return tasks.filter(canDeleteInspectionTask).map((task) => task.id);
}

export function activeTaskAfterDeletion(activeTask, deletedTasks) {
  if (!activeTask) return null;
  return deletedTasks.some((task) => task.id === activeTask.id) ? null : activeTask;
}

export function resultTaskForNavigation(tasks, activeTask) {
  if (activeTask) return activeTask;
  return tasks.find((task) => Number(task.completed_devices) > 0) ?? null;
}

export function describeTaskDeletion(tasks) {
  const scope = tasks.length === 1
    ? `巡检任务“${tasks[0].name}”`
    : `选中的 ${tasks.length} 个巡检任务`;
  // Deleting drops the device results and the stored raw output with the task,
  // so the prompt states both the scope and what deliberately survives.
  return `确定删除${scope}吗？任务的设备结果和原始回显将一并删除且无法恢复；已导出的报告和截图文件会保留。`;
}
