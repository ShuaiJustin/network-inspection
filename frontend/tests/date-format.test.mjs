import assert from "node:assert/strict";
import test from "node:test";

import { formatDateTime } from "../src/app/date-format.js";


test("legacy naive API timestamps are interpreted as UTC", () => {
  assert.equal(
    formatDateTime("2026-08-20T05:30:00"),
    formatDateTime("2026-08-20T05:30:00+00:00"),
  );
});


test("invalid or missing timestamps render as a dash", () => {
  assert.equal(formatDateTime("not-a-date"), "-");
  assert.equal(formatDateTime(null), "-");
  assert.equal(formatDateTime(""), "-");
});
