export function vendorOptions(catalog) {
  return (catalog?.vendors ?? []).map((vendor) => ({
    value: vendor.id,
    label: vendor.label || vendor.id,
  }));
}

export function vendorText(catalog, vendorId) {
  return (catalog?.vendors ?? []).find((vendor) => vendor.id === vendorId)?.label || vendorId;
}

export function platformOptions(catalog, vendorId) {
  const vendor = (catalog?.vendors ?? []).find((item) => item.id === vendorId);
  return (vendor?.platforms ?? []).map((platform) => ({
    value: platform.id,
    label: platform.label || platform.id,
  }));
}

export function deviceTypeOptions(catalog, vendorId) {
  const labels = new Map(
    (catalog?.device_types ?? []).map((deviceType) => [deviceType.id, deviceType.label]),
  );
  const vendor = vendorId
    ? (catalog?.vendors ?? []).find((item) => item.id === vendorId)
    : null;
  const ids = vendor?.device_types ?? (catalog?.device_types ?? []).map((item) => item.id);
  return ids.map((id) => ({ value: id, label: labels.get(id) || id }));
}

export function deviceTypeText(catalog, deviceType) {
  return (catalog?.device_types ?? []).find((item) => item.id === deviceType)?.label || deviceType;
}

export function defaultPlatformId(catalog, vendorId) {
  return (catalog?.vendors ?? []).find((item) => item.id === vendorId)?.default_platform_id || "";
}
