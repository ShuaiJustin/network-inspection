import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { translateApiMessage } from "../src/app/api-messages.js";
import { inspectionItemLabel } from "../src/app/inspection-items.js";

// Verbatim strings the backend puts in downgrade_reason / reference, i.e.
// exactly what the inspection result table renders for each check item.
const DOWNGRADE_REASONS = [
  "No verified collector is available for this item",
  "No verified SSH command is available for this item",
  "SSH output did not contain a supported value",
  "SNMP value unavailable",
  "Device rejected the SSH command as unsupported",
  "No active temperature alarms",
  "Active temperature alarm detected",
  "Active power_supplies alarm detected",
  "Active fans alarm detected",
  "No link aggregation is configured",
  "OSPF is not configured or has no neighbors",
  "No active device alarms",
  "Startup configuration file presence does not prove the current configuration is saved",
  "IF-MIB error counter columns unavailable: ifInErrors",
  "SNMP sysName/sysDescr returned an invalid value",
  "SNMP sysUpTime returned an invalid value",
  "SNMP metric collection failed",
  "SSH command failed: RuntimeError: command read timeout",
];

const REFERENCES = [
  "vendor CPU metric",
  "vendor memory metric",
  "vendor STP command or MIB",
  "vendor command or MIB",
  "ENTITY-MIB or vendor command",
  "ENTITY-SENSOR-MIB or vendor command",
];

test("the reason an item was downgraded is shown in Chinese", () => {
  // The result table mixed Chinese conclusions with English explanations, so a
  // row read "不支持 … No verified collector is available for this item".
  for (const message of DOWNGRADE_REASONS) {
    const translated = translateApiMessage(message);
    assert.notEqual(translated, message, `untranslated downgrade reason: ${message}`);
    // Protocol names stay verbatim; only the surrounding prose is translated.
    const prose = translated.replace(/SNMPv3|SNMP|SSH|MIB/g, "");
    assert.doesNotMatch(prose, /[A-Za-z]{4,}/, `English prose left in: ${translated}`);
  }
});

test("data-source references read as Chinese but keep the MIB names", () => {
  for (const reference of REFERENCES) {
    const translated = translateApiMessage(reference);
    assert.notEqual(translated, reference, `untranslated reference: ${reference}`);
    // MIB and protocol names are the identifiers printed in vendor
    // documentation, so they stay verbatim; only the prose is translated.
    const prose = translated.replace(/ENTITY-SENSOR-MIB|ENTITY-MIB|IF-MIB|SNMPv2-MIB|SNMP|MIB|CPU|STP/g, "");
    assert.doesNotMatch(prose, /[A-Za-z]{4,}/, `English prose left in: ${translated}`);
  }
});

test("standard MIB references are left exactly as the vendor documents them", () => {
  for (const reference of ["SNMPv2-MIB::sysName/sysDescr", "IF-MIB::ifNumber/ifXTable", "BRIDGE-MIB::dot1dStpPortState"]) {
    assert.equal(translateApiMessage(reference), reference);
  }
});


test("the result table renders the translated text, not the raw backend string", () => {
  // Translations only help if the table actually calls them; the row rendered
  // item.reference and item.downgradeReason verbatim.
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const row = app.slice(app.indexOf("{items.map((item)"), app.indexOf("</tbody>", app.indexOf("{items.map((item)")));
  assert.match(row, /inspectionReferenceText\(item\.reference\)/, "reference must be translated without the error heuristic");
  assert.match(row, /translateApiMessage\(item\.downgradeReason\)/, "downgrade reason must be translated");
  assert.match(row, /inspectionItemLabel\(item\.id\)/, "the check-item name must be shown in Chinese");
  assert.match(row, /formatInspectionItemValue\(item\.id, item\.value\)/, "the collected value must be shown");
});

test("the compact result view derives its finding from normalized inspection items", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  assert.match(app, /primaryInspectionFinding\(result\)/);
  assert.match(app, /inspectionItemRows\(metrics\)/);
  assert.doesNotMatch(app, /const alarming = \(metrics\.temperature/);
});

test("inspection item severity follows the frozen backend conclusion", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  assert.doesNotMatch(app, /metricAlert\(metrics\.(?:cpu|memory),\s*80\)/);
  assert.match(app, /<StatusPill value=\{item\.conclusion\} \/>/);
});

test("every inspection item the backend can report has a Chinese name", () => {
  // Item ids come straight from the backend definitions; a missing label would
  // print a bare identifier like "device_information" in the results table.
  const ITEM_IDS = [
    "device_information", "uptime", "cpu", "memory", "interfaces", "stp", "active_alarms",
    "boards", "power_supplies", "fans", "temperature", "interface_errors", "link_aggregation",
    "routing_neighbors", "ntp", "storage", "configuration_saved",
  ];
  for (const id of ITEM_IDS) {
    const label = inspectionItemLabel(id);
    assert.notEqual(label, id, `untranslated inspection item: ${id}`);
    assert.doesNotMatch(label, /[a-z_]{4,}/, `identifier left in: ${label}`);
  }
});
