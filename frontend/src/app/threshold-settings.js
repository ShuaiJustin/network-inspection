const THRESHOLD_PAIRS = [
  ["cpu_warning_threshold", "cpu_critical_threshold", "CPU"],
  ["memory_warning_threshold", "memory_critical_threshold", "内存"],
  ["storage_warning_threshold", "storage_critical_threshold", "存储空间"],
];

export function validateThresholdRanges(settings) {
  for (const [warningField, criticalField, label] of THRESHOLD_PAIRS) {
    if (settings[criticalField] < settings[warningField]) {
      return `${label} 严重阈值不能低于警告阈值`;
    }
  }
  return null;
}
