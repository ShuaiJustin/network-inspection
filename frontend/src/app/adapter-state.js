export function verifiedAdapterRows(records) {
  if (!Array.isArray(records)) return [];
  return records
    .filter((record) => record && record.status === "verified")
    .map((record) => ({
      vendor: String(record.vendor ?? "-"),
      modelRange: String(record.model_range ?? "-"),
      versionRange: String(record.version_range ?? "-"),
      inspectionItems: Array.isArray(record.inspection_items) ? record.inspection_items.map(String) : [],
    }));
}

export function adapterCapabilityRows(records) {
  if (!Array.isArray(records)) return [];
  return records
    .filter((record) => record && record.status === "unverified")
    .map((record) => ({
      vendor: String(record.vendor ?? "-"),
      platformId: String(record.platform_id ?? "-"),
      platformLabel: String(record.platform_label ?? "-"),
      status: "unverified",
      deviceTypes: Array.isArray(record.device_types) ? record.device_types.map(String) : [],
      protocols: Array.isArray(record.protocols) ? record.protocols.map(String) : [],
      backupCommand: String(record.backup_command ?? "-"),
      inspectionItems: Array.isArray(record.inspection_items) ? record.inspection_items.map(String) : [],
    }));
}

export function filterAdapterRows(rows, vendor, keyword) {
  const normalizedKeyword = String(keyword ?? "").trim().toLocaleLowerCase();
  return rows.filter((row) => {
    if (vendor && row.vendor !== vendor) return false;
    if (!normalizedKeyword) return true;
    const searchable = Object.values(row)
      .flatMap((value) => Array.isArray(value) ? value : [value])
      .join(" ")
      .toLocaleLowerCase();
    return searchable.includes(normalizedKeyword);
  });
}
