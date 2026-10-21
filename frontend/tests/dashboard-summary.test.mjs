import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("the dashboard abnormal-device count uses the latest task instead of cumulative history", () => {
  const dashboard = app.slice(app.indexOf("function Dashboard"), app.indexOf("function Metric"));
  assert.doesNotMatch(dashboard, /tasks\.reduce/);
  assert.match(dashboard, /tasks\[0\]\?\.failed_devices/);
  assert.match(dashboard, /最近任务异常/);
});

test("user-visible device and result tables render vendor labels", () => {
  assert.match(app, /vendorText\(catalog, device\.vendor\)/);
  assert.match(app, /vendorText\(catalog, result\.vendor\)/);
});
