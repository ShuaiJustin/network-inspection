export const SNMP_V3_AUTH_PROTOCOLS = ["SHA", "SHA256", "SHA224", "SHA384", "SHA512", "MD5"];
export const SNMP_V3_PRIV_PROTOCOLS = ["AES", "AES192", "AES256", "3DES", "DES"];
export const SNMP_V3_SECURITY_LEVELS = ["noAuthNoPriv", "authNoPriv", "authPriv"];
export const DEFAULT_SNMP_V3_SECURITY_LEVEL = "authPriv";

export function needsNewSnmpV3Secrets({
  isEditing,
  previousVersion,
  previousSecurityLevel,
  nextSecurityLevel,
  usernameChanged,
}) {
  if (nextSecurityLevel === "noAuthNoPriv") return false;
  return !isEditing
    || previousVersion !== "v3"
    || previousSecurityLevel !== nextSecurityLevel
    || usernameChanged;
}
