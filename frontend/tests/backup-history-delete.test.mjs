import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));

test("backup history offers row and selected deletion through the protected API", () => {
  assert.match(backupPage, /selectedBackupIds/);
  assert.match(backupPage, /toggleVisibleBackups/);
  assert.match(backupPage, /\/backup\/\$\{record\.id\}/);
  assert.match(backupPage, /method:\s*"DELETE"/);
  assert.match(backupPage, /onConfirm/);
  assert.match(backupPage, /删除选中记录/);
  assert.match(backupPage, /删除此记录/);
});

test("backup deletion warns that successful history also removes its file", () => {
  assert.match(backupPage, /同时删除.*备份文件/);
  assert.match(backupPage, /失败记录仅删除历史/);
});
