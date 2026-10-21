import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/app/types.ts", import.meta.url), "utf8");

test("firewall is a supported device type end to end", () => {
  assert.match(types, /device_type:\s*string/);
  assert.match(app, /deviceTypeText\(catalog,/);
});

test("the device dialog never renders raw English device types", () => {
  // the dialog Select must pass optionLabels, otherwise it shows switch/router
  const start = app.indexOf('<Select name="device_type"');
  assert.notEqual(start, -1, "device_type Select not found");
  const tag = app.slice(start, app.indexOf("/>", start) + 2);
  assert.match(tag, /optionLabels=/);
});

test("device type labels come from the backend catalog", () => {
  // a two-way ternary would silently mislabel firewalls as routers
  assert.equal(/device_type === "switch" \?/.test(app), false);
  assert.match(app, /deviceTypeText\(catalog,\s*device\.device_type\)/);
});
