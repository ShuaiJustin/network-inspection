export const RESULT_FILTERS = [
  { id: "all", label: "全部" },
  { id: "normal", label: "正常" },
  { id: "warning", label: "警告" },
  { id: "critical", label: "严重" },
  { id: "connection_failed", label: "连接失败" },
  { id: "authentication_failed", label: "认证失败" },
  { id: "data_fetch_failed", label: "数据获取失败" },
  { id: "unsupported", label: "不支持" },
  { id: "not_applicable", label: "不适用" },
  { id: "item_coverage_gap", label: "检查项未覆盖" },
  { id: "cancelled", label: "已取消" },
];

export const PRIMARY_RESULT_FILTERS = [
  { id: "all", label: "全部" },
  { id: "attention", label: "设备异常" },
  { id: "failed", label: "采集失败" },
  { id: "normal", label: "正常" },
];

const GROUPED_CONCLUSIONS = {
  attention: ["warning", "critical"],
  failed: ["connection_failed", "authentication_failed", "data_fetch_failed"],
};

const CONCLUSION_PRIORITY = [
  "critical",
  "warning",
  "authentication_failed",
  "connection_failed",
  "data_fetch_failed",
  "unsupported",
  "not_applicable",
  "cancelled",
  "normal",
];

export function hasUncoveredInspectionItems(result) {
  const items = result?.metrics?.inspection_items;
  if (!items || typeof items !== "object" || Array.isArray(items)) return false;
  return Object.values(items).some(
    (item) => item && typeof item === "object" && !Array.isArray(item) && item.conclusion === "unsupported",
  );
}

export function summarizeResults(results) {
  const count = (...conclusions) =>
    results.filter((result) => conclusions.includes(result.conclusion)).length;
  const unreachable = count("connection_failed", "authentication_failed");
  const dataFetchFailed = count("data_fetch_failed");
  return {
    total: results.length,
    normal: count("normal"),
    attention: count("warning", "critical"),
    unreachable,
    dataFetchFailed,
    failed: unreachable + dataFetchFailed,
    uncovered: count("unsupported", "not_applicable"),
    devicesWithUncoveredItems: results.filter(hasUncoveredInspectionItems).length,
    cancelled: count("cancelled"),
  };
}

export function filterResults(results, filter) {
  if (filter === "item_coverage_gap") return results.filter(hasUncoveredInspectionItems);
  if (GROUPED_CONCLUSIONS[filter]) {
    return results.filter((result) => GROUPED_CONCLUSIONS[filter].includes(result.conclusion));
  }
  return filter === "all" ? results : results.filter((result) => result.conclusion === filter);
}

export function resultFilterCount(results, filter) {
  return filterResults(results, filter).length;
}

export function sortResultsByAttention(results) {
  const priority = (conclusion) => {
    const index = CONCLUSION_PRIORITY.indexOf(conclusion);
    return index === -1 ? CONCLUSION_PRIORITY.length : index;
  };
  return [...results].sort((left, right) => priority(left.conclusion) - priority(right.conclusion));
}

export function primaryInspectionFinding(result) {
  const items = result?.metrics?.inspection_items;
  if (!items || typeof items !== "object" || Array.isArray(items)) return null;
  const ranked = Object.entries(items)
    .filter(([, item]) => item && typeof item === "object" && ["critical", "warning"].includes(item.conclusion))
    .sort(([, left], [, right]) => (left.conclusion === "critical" ? 0 : 1) - (right.conclusion === "critical" ? 0 : 1));
  if (!ranked.length) return null;
  const [id, item] = ranked[0];
  return { id, conclusion: item.conclusion, value: item.value };
}

// A device that failed to answer has no metrics. Printing 0 would claim the
// device reported a real value, so unknown values stay a dash.
export function formatMetric(value, suffix = "") {
  if (value === null || value === undefined || value === "") return "-";
  return `${value}${suffix}`;
}

export function taskProgressPercent(task) {
  const total = task?.total_devices ?? 0;
  if (!total) return 0;
  return Math.min(100, Math.round(((task.completed_devices ?? 0) / total) * 100));
}

// The detail panel is also the screenshot source, so it must never describe a
// device the active filter has hidden.
export function keepSelectionVisible(selectedId, visibleResults) {
  if (selectedId == null) return null;
  return visibleResults.some((result) => result.id === selectedId) ? selectedId : null;
}
