import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
const theme = readFileSync(new URL("../src/styles/theme.css", import.meta.url), "utf8");
const resultsPage = app.slice(app.indexOf("function ResultsPage"), app.indexOf("function AdaptersPage"));

test("the result list uses six operator-focused columns", () => {
  const header = resultsPage.match(/<table className="result-table">[\s\S]*?<thead><tr>([\s\S]*?)<\/tr><\/thead>/)?.[1] ?? "";
  assert.equal((header.match(/<th/g) ?? []).length, 6);
  for (const label of ["设备 / 管理 IP", "厂商", "结论", "关键发现", "采集时间", "操作"]) {
    assert.match(header, new RegExp(label.replace("/", "\\/")));
  }
});

test("result details live in one drawer instead of duplicate expanded sections", () => {
  assert.match(resultsPage, /className="result-drawer-layer"/);
  assert.match(resultsPage, /className="result-drawer"/);
  assert.doesNotMatch(resultsPage, /className="result-expanded"/);
  assert.equal((resultsPage.match(/设备巡检截图内容/g) ?? []).length, 1);
});

test("primary result filters show their matching counts", () => {
  assert.match(resultsPage, /PRIMARY_RESULT_FILTERS\.map/);
  assert.match(resultsPage, /resultFilterCount\(results, filter\.id\)/);
  assert.match(resultsPage, /sortResultsByAttention/);
});

test("the result drawer owns its scroll area", () => {
  assert.match(css, /\.result-drawer\s*\{[^}]*position:\s*fixed[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.result-drawer__body\s*\{[^}]*overflow-y:\s*auto/s);
});

test("the screenshot renderer is loaded only when the operator requests it", () => {
  assert.doesNotMatch(app, /^import html2canvas/m);
  assert.match(app, /await import\("html2canvas"\)/);
});

test("the shared rem scale keeps controls and text readable", () => {
  assert.match(theme, /--font-size:\s*16px/);
  assert.match(css, /\.dark \[data-slot="button"\]\.border\s*\{[^}]*background:\s*var\(--c-surface-muted\)/s);
});

test("wide settings fields span the current grid instead of creating a narrow third column", () => {
  assert.match(css, /\.setting--wide\s*\{[^}]*grid-column:\s*1\s*\/\s*-1/s);
  assert.match(app, /className="settings-section-heading setting--wide"[^>]*>采集策略/);
  assert.match(app, /className="settings-section-heading setting--wide"[^>]*>告警阈值/);
});

test("the device table combines metadata and exposes selected-device actions", () => {
  const devicesPage = app.slice(app.indexOf("function DevicesPage"), app.indexOf("function TasksPage"));
  const header = devicesPage.match(/<table className="device-table">[\s\S]*?<thead><tr>([\s\S]*?)<\/tr><\/thead>/)?.[1] ?? "";
  const literalHeaders = (header.match(/<th/g) ?? []).length;
  const sortableHeaders = (header.match(/sortHeader\(/g) ?? []).length;
  assert.equal(literalHeaders + sortableHeaders, 7);
  assert.match(devicesPage, /className="device-identity"/);
  assert.match(devicesPage, /className="device-meta-nowrap"/);
  assert.match(devicesPage, /className="device-bulk-bar"/);
  assert.match(devicesPage, /onNewTask/);
  assert.match(devicesPage, /onClearSelection/);
  assert.match(devicesPage, />发起巡检</);
});

test("long device and task forms keep their title and actions visible", () => {
  const deviceDialog = app.slice(app.indexOf("function DeviceDialog"), app.indexOf("function TaskDialog"));
  const taskDialog = app.slice(app.indexOf("function TaskDialog"), app.indexOf("function Field"));
  for (const dialog of [deviceDialog, taskDialog]) {
    assert.match(dialog, /className="dialog dialog--scrollable"/);
    assert.match(dialog, /className="dialog-body"/);
  }
  assert.match(css, /\.dialog--scrollable\s*\{[^}]*overflow:\s*hidden/s);
  assert.match(css, /\.dialog-body\s*\{[^}]*overflow-y:\s*auto/s);
});

test("switching SNMP modes remounts the credential fields cleanly", () => {
  const deviceDialog = app.slice(app.indexOf("function DeviceDialog"), app.indexOf("function TaskDialog"));
  assert.match(deviceDialog, /key="snmp-v2c"/);
  assert.match(deviceDialog, /key="snmp-v3"/);
  assert.match(deviceDialog, /type="password"[^>]*autoComplete="new-password"/);
});

test("dashboard actions open the relevant workflows directly", () => {
  const dashboard = app.slice(app.indexOf("function Dashboard"), app.indexOf("function Metric"));
  assert.match(dashboard, /onNewDevice/);
  assert.match(dashboard, /onNewTask/);
  assert.match(dashboard, />新建巡检</);
});
