import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const types = readFileSync(new URL("../src/app/types.ts", import.meta.url), "utf8");
test("every connection-test status has a Chinese label", () => {
  // ConnectionTestResult["status"] is a closed union; each member
  // is rendered through conclusionText, which fell back to the raw English key.
  const union = types.match(/status: ("(?:success|connection_failed|authentication_failed|data_fetch_failed)"(?:\s*\|\s*"[a-z_]+")*);/);
  assert.ok(union, "expected the ConnectionTestResult status union");
  const statuses = [...union[1].matchAll(/"([a-z_]+)"/g)].map((m) => m[1]);
  assert.ok(statuses.length >= 4);
  const table = app.slice(app.indexOf("const conclusionText"), app.indexOf("function StatusPill"));
  for (const status of statuses) {
    assert.match(table, new RegExp(`\\b${status}:`), `conclusionText is missing ${status}`);
  }
});

test("unsupported, not-applicable, and cancelled results use a neutral status style", () => {
  const statusPill = app.slice(app.indexOf("function StatusPill"), app.indexOf("function App"));
  assert.match(statusPill, /unsupported/);
  assert.match(statusPill, /not_applicable/);
  assert.match(statusPill, /cancelled/);
  assert.match(statusPill, /neutral/);
});
