import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

const conclusionMap = () => {
  const start = app.indexOf("const conclusionText");
  return app.slice(start, app.indexOf("};", start));
};

test("every conclusion the backend can produce has a Chinese label", () => {
  // \u00a77.4 of the requirements: a device ends as one of these states, and
  // \u00a75.1 forbids showing raw storage values to the operator.
  const backendValues = [
    "normal", "warning", "critical",
    "connection_failed", "authentication_failed", "data_fetch_failed",
    "cancelled", "unsupported", "not_applicable",
  ];
  const map = conclusionMap();
  for (const value of backendValues) {
    assert.match(map, new RegExp(`\\b${value}:`), `missing Chinese label for "${value}"`);
  }
});

test("every task status has a Chinese label", () => {
  const map = conclusionMap();
  for (const value of ["queued", "running", "completed", "partial_failed", "failed", "cancelled"]) {
    assert.match(map, new RegExp(`\\b${value}:`), `missing Chinese label for "${value}"`);
  }
});
