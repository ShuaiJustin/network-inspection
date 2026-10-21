import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

test("every custom modal and the result drawer are labelled and use shared keyboard behavior", () => {
  const modalCount = (app.match(/className="modal-layer"/g) ?? []).length;
  const labelledDialogCount = (app.match(/role="dialog" aria-modal="true" aria-labelledby=/g) ?? []).length;
  const behaviorCount = (app.match(/const dialogRef = useModalBehavior\(onClose/g) ?? []).length;

  assert.equal(modalCount, 5);
  assert.equal(labelledDialogCount, modalCount + 1);
  assert.equal(behaviorCount, modalCount + 1);
  assert.equal((app.match(/role="dialog"[^>]*tabIndex=\{-1\}/g) ?? []).length, modalCount + 1);

  const behavior = app.slice(app.indexOf("function useModalBehavior"), app.indexOf("function Dashboard"));
  assert.match(behavior, /event\.key === "Escape"/);
  assert.match(behavior, /event\.key !== "Tab"/);
  assert.match(behavior, /document\.addEventListener\("keydown", handleKeyDown\)/);
  assert.match(behavior, /!dialog\.contains\(document\.activeElement\)/);
  assert.match(behavior, /previous\?\.focus\(\)/);
});

test("submitting dialogs cannot be dismissed with Escape behind disabled controls", () => {
  const behavior = app.slice(app.indexOf("function useModalBehavior"), app.indexOf("function Dashboard"));
  const passwordDialog = app.slice(app.indexOf("function PasswordChangeDialog"), app.indexOf("function LogsPage"));
  const deviceDialog = app.slice(app.indexOf("function DeviceDialog"), app.indexOf("function TaskDialog"));
  const taskDialog = app.slice(app.indexOf("function TaskDialog"), app.indexOf("function Field"));

  assert.match(behavior, /closeDisabledRef/);
  assert.match(behavior, /if \(!closeDisabledRef\.current\) closeRef\.current\(\)/);
  for (const dialog of [passwordDialog, deviceDialog, taskDialog]) {
    assert.match(dialog, /useModalBehavior\(onClose, submitting\)/);
  }
});

test("native file inputs remain keyboard reachable and expose a visible focus ring", () => {
  assert.equal((app.match(/<input type="file"/g) ?? []).length, 2);
  assert.doesNotMatch(css, /\.button-label input\s*\{[^}]*display:\s*none/);
  assert.match(css, /\.button-label:focus-within\s*\{[^}]*outline:/);
});
