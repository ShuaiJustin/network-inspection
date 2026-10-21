import assert from "node:assert/strict";
import test from "node:test";

import {
  DEFAULT_SNMP_V3_SECURITY_LEVEL,
  SNMP_V3_AUTH_PROTOCOLS,
  SNMP_V3_PRIV_PROTOCOLS,
  needsNewSnmpV3Secrets,
} from "../src/app/snmp-v3.js";

test("SNMPv3 form exposes every backend-supported authentication and privacy protocol", () => {
  assert.deepEqual(SNMP_V3_AUTH_PROTOCOLS, ["SHA", "SHA256", "SHA224", "SHA384", "SHA512", "MD5"]);
  assert.deepEqual(SNMP_V3_PRIV_PROTOCOLS, ["AES", "AES192", "AES256", "3DES", "DES"]);
});

test("new SNMPv3 devices default to encrypted authPriv", () => {
  assert.equal(DEFAULT_SNMP_V3_SECURITY_LEVEL, "authPriv");
});

test("changing a stored SNMPv3 security level or username requires replacement secrets", () => {
  assert.equal(needsNewSnmpV3Secrets({
    isEditing: true,
    previousVersion: "v3",
    previousSecurityLevel: "authNoPriv",
    nextSecurityLevel: "authPriv",
    usernameChanged: false,
  }), true);
  assert.equal(needsNewSnmpV3Secrets({
    isEditing: true,
    previousVersion: "v3",
    previousSecurityLevel: "authPriv",
    nextSecurityLevel: "authPriv",
    usernameChanged: true,
  }), true);
  assert.equal(needsNewSnmpV3Secrets({
    isEditing: true,
    previousVersion: "v3",
    previousSecurityLevel: "authPriv",
    nextSecurityLevel: "authPriv",
    usernameChanged: false,
  }), false);
});
