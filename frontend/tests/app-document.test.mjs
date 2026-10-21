import assert from "node:assert/strict";
import { existsSync, readFileSync } from "node:fs";
import test from "node:test";

test("the application document declares a favicon that is included in the build", () => {
  const document = readFileSync(new URL("../index.html", import.meta.url), "utf8");

  assert.match(document, /<link rel="icon" href="\/favicon\.png"/);
  assert.equal(existsSync(new URL("../public/favicon.png", import.meta.url)), true);
});

test("the application document shows startup feedback before React mounts", () => {
  const document = readFileSync(new URL("../index.html", import.meta.url), "utf8");

  assert.match(document, /class="startup-shell"[^>]*role="status"[^>]*aria-live="polite"/);
  assert.match(document, /class="startup-shell__progress"/);
  assert.match(document, /正在启动网络巡检/);
  assert.match(document, /正在准备本机巡检服务/);
});

test("React render failures show a recoverable error screen", () => {
  const main = readFileSync(new URL("../src/main.tsx", import.meta.url), "utf8");

  assert.match(main, /class AppErrorBoundary/);
  assert.match(main, /<AppErrorBoundary>\s*<App \/>\s*<\/AppErrorBoundary>/);
  assert.match(main, /界面加载失败/);
  assert.match(main, /window\.location\.reload\(\)/);
});
