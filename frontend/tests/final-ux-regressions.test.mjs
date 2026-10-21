import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

test("destructive and trust actions use the themed confirmation dialog", () => {
  assert.doesNotMatch(app, /window\.confirm\(/);
  assert.doesNotMatch(app, /window\.alert\(/);
  assert.match(app, /function ConfirmationDialog/);
  assert.match(app, /aria-labelledby="confirmation-dialog-title"/);
  assert.match(app, /await confirmAction\(/);
});

test("a new notice cannot remain hidden behind a stale global error", () => {
  const notifyStart = app.indexOf("const notify =");
  const notifyEnd = app.indexOf("const invalidateWorkspaceRequests", notifyStart);
  const notify = app.slice(notifyStart, notifyEnd);

  assert.match(notify, /setError\(null\)/);
  assert.match(notify, /setNotice\(\{ message, tone \}\)/);
});

test("switching workbench pages returns the content pane to the top", () => {
  assert.match(app, /const contentRef = useRef<HTMLDivElement \| null>\(null\)/);
  assert.match(app, /contentRef\.current\?\.scrollTo\(\{ top: 0/);
  assert.match(app, /<div ref=\{contentRef\} className="content">/);
});

test("settings validation is rendered inline instead of blocking the window", () => {
  const settings = app.slice(app.indexOf("function SettingsPage"), app.indexOf("function PasswordChangeDialog"));
  assert.match(settings, /settingsError/);
  assert.match(settings, /role="alert"/);
});
