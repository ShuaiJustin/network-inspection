export const CLEARABLE_TEXT_FIELDS = ["name", "group_name", "note"];

const SECRET_FIELDS = [
  "ssh_password",
  "enable_password",
  "snmp_community",
  "snmp_v3_auth_key",
  "snmp_v3_priv_key",
];

/**
 * Build the device payload from the raw form entries.
 *
 * Blank secrets mean "keep the stored value" and must be dropped, but blank
 * optional text fields mean "clear this value" and must be sent as null -
 * dropping them makes the backend keep the old value (exclude_unset=True).
 */
export function buildDevicePayload(entries, { isEditing, clearEnablePassword = false } = {}) {
  const payload = { ...entries };
  for (const [key, value] of Object.entries(payload)) {
    if (value !== "") continue;
    if (isEditing && CLEARABLE_TEXT_FIELDS.includes(key)) {
      payload[key] = null;
    } else {
      delete payload[key];
    }
  }
  if (isEditing && clearEnablePassword) payload.enable_password = null;
  return payload;
}

export function isSecretField(field) {
  return SECRET_FIELDS.includes(field);
}
