const EXPLICIT_TIMEZONE = /(?:Z|[+-]\d{2}:\d{2})$/;

export function formatDateTime(value) {
  if (value === null || value === undefined) return "-";
  const text = String(value).trim();
  if (!text) return "-";

  // Older API responses contained naive UTC values. Keep accepting them while
  // treating new values with an explicit offset according to their offset.
  const normalized = EXPLICIT_TIMEZONE.test(text)
    ? text
    : `${text.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? "-" : date.toLocaleString("zh-CN");
}
