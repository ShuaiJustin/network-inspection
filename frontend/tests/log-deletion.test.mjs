import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("log deletion supports selected rows and the current filtered snapshot", () => {
  const logsPage = app.slice(app.indexOf("function LogsPage"), app.indexOf("function DeviceDialog"));
  assert.match(logsPage, /onConfirm:\s*\(options:\s*ConfirmationOptions\)/);
  assert.match(logsPage, /const \[selectedLogIds, setSelectedLogIds\]/);
  assert.match(logsPage, /const \[deletingLogs, setDeletingLogs\]/);
  assert.match(logsPage, /method: "DELETE"/);
  assert.match(logsPage, /delete_all/);
  assert.match(logsPage, /before_id/);
  assert.match(logsPage, /删除所选/);
  assert.match(logsPage, /清空当前筛选/);
  assert.match(logsPage, /选择当前页日志/);
  assert.match(logsPage, /title=\{item\.message/);
  assert.match(app, /<LogsPage onConfirm=\{confirmAction\}/);
});
