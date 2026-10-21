import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const apiClient = readFileSync(new URL("../src/app/api-client.ts", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const cargo = readFileSync(new URL("../../src-tauri/Cargo.toml", import.meta.url), "utf8");
const permissions = readFileSync(new URL("../../src-tauri/capabilities/default.json", import.meta.url), "utf8");

test("desktop export uses a native save result and reports its destination", () => {
  assert.match(apiClient, /isTauri/);
  assert.match(apiClient, /@tauri-apps\/plugin-dialog/);
  assert.match(apiClient, /@tauri-apps\/plugin-fs/);
  assert.match(apiClient, /await save\(/);
  assert.match(apiClient, /await writeFile\(/);
  assert.match(apiClient, /status: "cancelled"/);
  assert.match(apiClient, /status: "saved"/);

  assert.match(app, /const \[exportingReport, setExportingReport\]/);
  assert.match(app, /const exportingReportRef = useRef\(false\)/);
  assert.match(app, /导出中…/);
  assert.match(app, /巡检报告已保存/);
  assert.match(cargo, /tauri-plugin-dialog/);
  assert.match(cargo, /tauri-plugin-fs/);
  assert.match(permissions, /dialog:allow-save/);
  assert.match(permissions, /fs:allow-write-file/);
});
