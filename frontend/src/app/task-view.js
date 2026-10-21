export const TASK_FILTERS = [
  { id: "all", label: "全部" },
  { id: "active", label: "进行中" },
  { id: "completed", label: "已完成" },
  { id: "partial_failed", label: "部分失败" },
  { id: "failed", label: "执行失败" },
  { id: "cancelled", label: "已取消" },
];

const ACTIVE_STATUSES = ["queued", "running"];
const ATTENTION_STATUSES = ["partial_failed", "failed", "cancelled"];

function isActiveTask(task) {
  return ACTIVE_STATUSES.includes(task.status)
    || (task.status === "cancelled" && !task.finished_at);
}

export function summarizeTasks(tasks) {
  return {
    total: tasks.length,
    active: tasks.filter(isActiveTask).length,
    completed: tasks.filter((task) => task.status === "completed").length,
    attention: tasks.filter((task) => !isActiveTask(task) && ATTENTION_STATUSES.includes(task.status)).length,
  };
}

export function filterTasks(tasks, filter) {
  if (filter === "all") return tasks;
  if (filter === "active") return tasks.filter(isActiveTask);
  return tasks.filter((task) => task.status === filter);
}

export function partitionVisibleTasks(tasks) {
  return {
    active: tasks.filter(isActiveTask),
    history: tasks.filter((task) => !isActiveTask(task)),
  };
}

export function taskProgressTone(status) {
  if (status === "running") return "active";
  if (status === "completed") return "success";
  if (status === "partial_failed") return "warning";
  if (status === "failed") return "danger";
  return "neutral";
}
