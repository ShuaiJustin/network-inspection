import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

test("raw output readability uses code colors and user-controlled wrapping", () => {
  assert.match(css, /--c-code-text:\s*#[0-9a-f]{6}/i);
  assert.match(css, /\.raw-output pre[^}]*color:\s*var\(--c-code-text\)/s);
  assert.match(css, /\.diff-panel pre[^}]*color:\s*var\(--c-code-text\)/s);
  assert.match(css, /white-space:\s*pre;/);
  assert.match(app, /const \[wrapRawOutput, setWrapRawOutput\]/);
  assert.match(app, /复制输出/);
  assert.match(app, /自动换行/);
  assert.match(app, /正在读取原始采集输出/);
});

test("result drawer receives every raw output control from the results page", () => {
  const drawerCall = app.slice(
    app.indexOf("{selectedResult && <ResultDrawer"),
    app.indexOf("function ResultDrawer"),
  );
  const drawer = app.slice(
    app.indexOf("function ResultDrawer"),
    app.indexOf("function AdaptersPage"),
  );

  assert.match(drawerCall, /loadingRawOutput=\{loadingRawOutput\}/);
  assert.match(drawerCall, /wrapRawOutput=\{wrapRawOutput\}/);
  assert.match(drawerCall, /onToggleRawOutputWrap=/);
  assert.match(drawerCall, /onCopyRawOutput=/);
  assert.match(drawer, /loadingRawOutput/);
  assert.match(drawer, /wrapRawOutput/);
  assert.match(drawer, /onToggleRawOutputWrap/);
  assert.match(drawer, /onCopyRawOutput/);
  assert.doesNotMatch(drawer, /setWrapRawOutput|copyRawOutput/);
});
