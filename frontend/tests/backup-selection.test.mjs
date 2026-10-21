import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("backup page selection is local and explains SSH readiness", () => {
  const backupPage = app.slice(app.indexOf("function BackupPage"), app.indexOf("function SettingsPage"));
  assert.match(backupPage, /devices:\s*Device\[\]/);
  assert.match(backupPage, /onBackup:\s*\(deviceIds:\s*number\[\]\)/);
  assert.match(backupPage, /const \[backupDeviceIds, setBackupDeviceIds\]/);
  assert.match(backupPage, /device\.has_ssh/);
  assert.match(backupPage, /未配置 SSH/);
  assert.match(backupPage, /选择全部可备份设备/);
  assert.match(backupPage, /await onBackup\(backupDeviceIds\)/);
  assert.doesNotMatch(backupPage, /请先在设备管理中勾选设备/);
  assert.match(app, /<BackupPage devices=\{devices\}/);
});
