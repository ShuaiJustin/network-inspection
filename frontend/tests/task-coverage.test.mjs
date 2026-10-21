import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("task rows explain completed devices that are neither success nor failure", () => {
  const table = app.slice(app.indexOf("function TaskTable"), app.indexOf("function ResultsPage"));
  assert.match(table, /unsupported_devices/);
  assert.match(table, /not_applicable_devices/);
  assert.match(table, /cancelled_devices/);
  assert.match(table, /未覆盖/);
  assert.match(table, /结果分布/);
});

test("collection success is not presented as device health", () => {
  assert.doesNotMatch(app, /最近巡检成功/);
  assert.match(app, /最近采集完成/);
  assert.match(app, /已采集 \{task\.success_devices\}/);
  assert.match(app, /采集失败 \{task\.failed_devices\}/);
});

test("result overview surfaces uncovered and cancelled devices", () => {
  const results = app.slice(app.indexOf("function ResultsPage"), app.indexOf("function AdaptersPage"));
  assert.match(results, /summary\.uncovered/);
  assert.match(results, /summary\.devicesWithUncoveredItems/);
  assert.match(results, /summary\.cancelled/);
  assert.match(results, /未覆盖/);
});
