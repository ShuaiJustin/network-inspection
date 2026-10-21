import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import { importIssueRows } from "../src/app/import-errors.js";

test("Excel import issues retain every row number and reason", () => {
  assert.deepEqual(importIssueRows([
    { row: 2, reason: "Management IP already exists" },
    { row: 7, reason: "SNMPv3 requires username and security level" },
  ]), [
    { row: 2, reason: "该管理 IP 已存在" },
    { row: 7, reason: "SNMPv3 需要用户名和安全级别" },
  ]);
});

test("the application renders an import issue dialog instead of only an error count", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  assert.match(app, /ImportErrorsDialog/);
  assert.match(app, /第\s*\{issue\.row\}\s*行/);
  assert.match(app, /issue\.reason/);
});
