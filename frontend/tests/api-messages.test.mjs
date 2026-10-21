import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { translateApiMessage, userErrorMessage } from "../src/app/api-messages.js";

const hasChinese = (value) => /[\u4e00-\u9fff]/.test(value);

test("the reported bug: a wrong password is reported in Chinese", () => {
  const shown = translateApiMessage("Invalid username or password");
  assert.ok(hasChinese(shown), `still English: ${shown}`);
  assert.equal(/[A-Za-z]{4,}/.test(shown), false, "no English words should remain");
});

test("every error the backend can return is translated", () => {
  // kept in sync with the detail= strings in backend/app
  const backendMessages = [
    "Administrator login required",
    "Administrator has already been initialized",
    "Invalid username or password",
    "Current password is incorrect",
    "Local runtime token is required",
    "One or more devices were not found",
    "One or more active devices were not found",
    "Valid backup not found",
    "Backup file is no longer available",
    "Could not open backup directory",
    "Backup record not found",
    "Backup file is outside the configured backup root",
    "Could not delete backup file",
    "Management IP already exists",
    "Device not found",
    "SSH credentials not configured",
    "At least one complete SNMP or SSH credential set is required",
    "Please upload an .xlsx file",
    "Excel import file is too large",
    "Task not found",
    "Finished task cannot be cancelled",
    "Only finished tasks can be retried",
    "No active devices from the original task remain",
    "Running task must be cancelled before it can be deleted",
    "Inspection report is only available after task completion",
    "Inspection result not found",
    "Screenshot must be a PNG image",
    "Screenshot is too large",
    "Invalid SQLite backup file",
    "SQLite backup failed integrity check",
    "Backup file does not match the inspection database schema",
    "Database restore requires confirmation",
    "Please upload an SQLite backup file",
    "Database backup file is too large",
    "Cannot restore while an inspection task is running",
    "Cannot restore while a configuration backup is running",
    "SNMPv3 requires username and security level",
    "SNMPv3 authNoPriv requires authentication protocol and key",
    "SNMPv3 authPriv requires authentication and privacy protocols and keys",
    "Backup ignore pattern is too long",
    "Unsupported vendor: Unknown",
    "Unsupported platform: unknown-os",
    "Platform cisco-ios does not belong to vendor Huawei",
    "Device type firewall is not supported by platform example-os",
  ];
  const untranslated = backendMessages.filter((message) => !hasChinese(translateApiMessage(message)));
  assert.deepEqual(untranslated, [], "these messages would reach the operator in English");
});

test("messages that embed a variable part are still translated", () => {
  for (const message of [
    "Invalid Excel file: bad zip",
    "Missing required Excel columns: ip, vendor",
    "Invalid backup ignore pattern: bad escape",
    "Report directory is not writable",
    "CPU critical threshold must not be lower than warning threshold",
    "Unexpected collection error: RuntimeError: boom",
  ]) {
    assert.ok(hasChinese(translateApiMessage(message)), `not translated: ${message}`);
  }
});

test("backup validation failures explain why the captured configuration is unsafe", () => {
  const cases = new Map([
    ["Configuration output contains a paging marker and may be incomplete", "配置回显包含分页提示，可能不完整"],
    ["Configuration output contains a truncated output notice", "配置回显包含截断提示"],
    ["Configuration output contains an unterminated banner and is truncated", "配置回显中的横幅未结束，内容已截断"],
    ["Configuration validation does not support the selected platform", "当前平台不支持配置完整性校验"],
    ["Configuration output has no recognized configuration structure for this platform", "配置回显不符合当前平台的配置结构"],
    ["Configuration output is missing the expected configuration structure", "配置回显缺少必要的配置结构"],
    ["Configuration output is truncated or missing its final return marker", "配置回显已截断或缺少末尾 return 标记"],
    ["Configuration output is truncated or missing its final end marker", "配置回显已截断或缺少末尾 end 标记"],
  ]);

  for (const [message, expected] of cases) {
    assert.equal(translateApiMessage(message), expected);
  }
});

test("historic SSH decoding failures explain that a retry is required", () => {
  assert.equal(
    translateApiMessage("SSH command output decoding failed: 'utf-8' codec can't decode byte 0xb9"),
    "该次备份的 SSH 输出编码无法识别；新版已兼容常见中文编码，请重新执行备份",
  );
});

test("SNMP bounds and malformed table errors are translated", () => {
  assert.equal(
    translateApiMessage("SNMP device collection exceeded elapsed time limit (60s)"),
    "SNMP 单设备采集超过总时间上限",
  );
  assert.equal(
    translateApiMessage("SNMP table exceeded elapsed time limit (20s)"),
    "SNMP 表遍历超过时间上限",
  );
  assert.equal(
    translateApiMessage("SNMP table exceeded row limit (4096)"),
    "SNMP 表遍历超过行数上限",
  );
  assert.equal(
    translateApiMessage("SNMP table returned a non-increasing OID"),
    "SNMP 表返回了非递增 OID",
  );
});

test("activity log levels, events, and generated messages are translated", () => {
  for (const message of [
    "INFO",
    "WARNING",
    "ERROR",
    "backup_deleted",
    "task_finished",
    "device_connectivity_test",
    "No previous valid backup",
    "Backup completed for 2 devices",
    "Configuration backup record deleted",
    "Task 每日巡检 started",
    "Task finished: cancelled",
    "Database backup created: network_inspection_20260804.db",
  ]) {
    assert.ok(hasChinese(translateApiMessage(message)), `not translated: ${message}`);
  }
});

test("backup and log pages translate persisted backend text before rendering", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

  assert.match(app, /record\.message \? translateApiMessage\(record\.message\) : "-"/);
  assert.match(app, /translateApiMessage\(diffMessage\)/);
  assert.match(app, /translateApiMessage\(item\.level\)/);
  assert.match(app, /translateApiMessage\(item\.event\)/);
  assert.match(app, /item\.message \? translateApiMessage\(item\.message\) : "-"/);
});

test("API and result errors use the operator-safe Chinese error formatter", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const apiClient = readFileSync(new URL("../src/app/api-client.ts", import.meta.url), "utf8");
  const describeApiError = apiClient.slice(apiClient.indexOf("export function describeApiError"), apiClient.indexOf("function clearExpiredSession"));
  const resultPresentation = app.slice(app.indexOf("function resultFindingText"), app.indexOf("function AdaptersPage"));

  assert.match(app, /import \{ translateApiMessage, userErrorMessage \}/);
  assert.match(describeApiError, /userErrorMessage\(detail, fallback\)/);
  assert.match(resultPresentation, /userErrorMessage\(result\.message/);
  assert.doesNotMatch(resultPresentation, /result\.message \? translateApiMessage\(result\.message\)/);
});

test("combined SNMP and SSH failure reasons are translated independently", () => {
  const shown = translateApiMessage("SNMP: SNMP credentials not configured; SSH: timed out");

  assert.equal(shown, "SNMP：未配置 SNMP 凭据；SSH：连接设备超时，请确认 IP、SSH 端口与链路");
  assert.doesNotMatch(shown, /credentials|configured|timed out/i);
});

test("skipped SSH supplementary collection is translated", () => {
  const shown = translateApiMessage(
    "SSH supplementary collection skipped: SSH credentials not configured",
  );

  assert.equal(shown, "未配置 SSH 凭据，已跳过 SSH 补充采集");
});

test("an unknown message is passed through unchanged", () => {
  assert.equal(translateApiMessage("something entirely new"), "something entirely new");
});

test("an unknown English error is never used as the primary operator message", () => {
  const shown = userErrorMessage(
    new Error("The collector went sideways in an entirely new way"),
    "\u64cd\u4f5c\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5",
  );

  assert.equal(shown, "\u64cd\u4f5c\u5931\u8d25\uff0c\u8bf7\u91cd\u8bd5");
  assert.doesNotMatch(shown, /collector|sideways/i);
});

test("known and already-Chinese errors retain their actionable wording", () => {
  assert.equal(userErrorMessage(new Error("Invalid username or password"), "\u767b\u5f55\u5931\u8d25"), "\u7528\u6237\u540d\u6216\u5bc6\u7801\u9519\u8bef");
  assert.equal(userErrorMessage(new Error("\u65e0\u6cd5\u8fde\u63a5\u672c\u673a\u5de1\u68c0\u670d\u52a1"), "\u64cd\u4f5c\u5931\u8d25"), "\u65e0\u6cd5\u8fde\u63a5\u672c\u673a\u5de1\u68c0\u670d\u52a1");
});

test("an unmapped English runtime error is replaced with an actionable Chinese message", () => {
  const shown = translateApiMessage("[WinError 10061] Connection refused by remote host");

  assert.match(shown, /[\u4e00-\u9fff]/);
  assert.doesNotMatch(shown, /WinError|Connection refused/i);
});

test("non-string input is returned untouched", () => {
  assert.equal(translateApiMessage(undefined), undefined);
  assert.equal(translateApiMessage(null), null);
});
