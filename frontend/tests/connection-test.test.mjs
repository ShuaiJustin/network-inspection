import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { translateApiMessage } from "../src/app/api-messages.js";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

// Verbatim pysnmp errind strings (enumerated from the installed library), i.e.
// exactly what the collector hands back as `message` for a failed probe.
const SNMP_REASONS = [
  "No SNMP response received before timeout",
  "Unknown SNMP community name encountered",
  "Wrong SNMP PDU digest",
  "Unknown USM user",
  "Unknown SNMP security name encountered",
  "Authenticator mismatched",
  "SNMP message timing parameters not in windows of trust",
  "Unsupported SNMP security level",
  "Authentication protocol is not supported",
  "Privacy protocol is not supported",
  "Ciphering services not available or bad parameters",
  "Ciphering services not available or ciphertext is broken",
  "Empty SNMP response message",
  "SNMP engine ID mismatch encountered",
  "Access to MIB node denied",
  "Requested OID is out of MIB view",
];

// Verbatim paramiko / netmiko messages reachable from the SSH probe.
const SSH_REASONS = [
  "Authentication failed.",
  "No existing session",
];

test("every SNMP probe failure reason has a Chinese rendering", () => {
  for (const message of SNMP_REASONS) {
    const translated = translateApiMessage(message);
    assert.notEqual(translated, message, `untranslated SNMP reason: ${message}`);
    // Protocol nouns (SNMP, SNMPv3, Community, MIB, OID, IP, VACM, SSH) stay in
    // English on purpose - they are the terms printed in vendor documentation.
    const leftover = translated.replace(/SNMPv3|SNMP|Community|MIB|OID|VACM|SSH|IP|USM|TCP/g, "");
    assert.doesNotMatch(leftover, /[A-Za-z]{3,}/, `English prose left in: ${translated}`);
  }
});

test("SSH probe failure reasons have a Chinese rendering", () => {
  for (const message of SSH_REASONS) {
    const translated = translateApiMessage(message);
    assert.notEqual(translated, message, `untranslated SSH reason: ${message}`);
  }
});

test("device state column does not reuse inspection conclusion wording", () => {
  // `is_active` is an operator-controlled enable/disable flag that says nothing
  // about reachability. Rendering it through StatusPill's "normal" key labels a
  // never-contacted device 正常, which reads as a verified health result.
  assert.doesNotMatch(
    app,
    /StatusPill value=\{device\.is_active \? "normal" : "queued"\}/,
    "enable flag must not borrow the inspection conclusion vocabulary",
  );
});

test("connectivity test reports progress while the probe is running", () => {
  // SNMP timeout is configurable to 60s with 5 retries, so one probe can run
  // for minutes. Without an in-flight message the operator only sees a dimmed
  // icon and cannot tell a slow probe from a hung application.
  assert.match(app, /notify\([^;]*正在测试[^;]*"progress"\)/, "expected an in-flight notice before awaiting the probe");
});

test("a failed probe is shown as an error, not an informational notice", () => {
  // The probe reports success and failure through the same banner, so a
  // connection failure once looked identical to a successful test.
  const body = app.slice(app.indexOf("const testConnection ="), app.indexOf("const importExcel ="));
  assert.match(body, /result\.status === "success"/, "expected the outcome to branch on success");
  assert.match(body, /setError\(/, "a failed probe must surface through the error banner");
});

test("concurrent probes track separate busy keys and remove only themselves", () => {
  const body = app.slice(app.indexOf("const testConnection ="), app.indexOf("const importExcel ="));
  const devices = app.slice(app.indexOf("function DevicesPage"), app.indexOf("function TasksPage"));

  assert.match(app, /useState<Set<string>>/);
  assert.match(body, /testingConnectionsRef\.current\.add\(key\)/);
  assert.match(body, /removePendingKey\(current, key\)/);
  assert.match(devices, /testingConnections\.has\(`/);
});

test("a host-key confirmation collision names the affected device in a warning", () => {
  const body = app.slice(app.indexOf("const testConnection ="), app.indexOf("const importExcel ="));

  assert.match(app, /return Promise\.resolve\(null\)/);
  assert.match(body, /if \(confirmed === null\) \{[\s\S]*\$\{deviceName\}[\s\S]*"warning"[\s\S]*return;/);
  assert.match(body, /isOperationCurrent\(requestEpoch\)/);
});
