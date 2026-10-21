import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import { noticeAutoDismissMs } from "../src/app/notice-tone.js";

const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
const rule = readFileSync(new URL("../src/app/notice-tone.js", import.meta.url), "utf8");

function callSite(marker) {
  const start = app.indexOf(marker);
  assert.notEqual(start, -1, `call site not found: ${marker}`);
  return app.slice(start, start + 1000);
}

test("the tone is declared by the caller, never guessed from the wording", () => {
  // The reported bug: 巡检任务"…"已创建，正在执行 stayed pinned to the top of the
  // page after the run had finished. The banner had matched /正在执行/ and
  // classified a finished action as work still in flight. Any rule that reads
  // the message text will keep mislabelling new wording, so the tone has to
  // travel with the message instead.
  assert.doesNotMatch(rule, /SUCCESS_PATTERNS|PROGRESS_PATTERNS|WARNING_PATTERNS/);
  assert.doesNotMatch(rule, /export function noticeTone/);
  assert.doesNotMatch(app, /noticeTone\(/);
});

test("only the tone decides how long the banner stays", () => {
  assert.equal(noticeAutoDismissMs("success"), 4000);
  // Work in flight tracks something still running, a warning lists rows the
  // operator has to fix, and an error must be read, so none of them time out.
  assert.equal(noticeAutoDismissMs("progress"), null);
  assert.equal(noticeAutoDismissMs("warning"), null);
  assert.equal(noticeAutoDismissMs("error"), null);
});

test("creating a task is a finished action and clears itself", () => {
  // Submitting the task is complete once the backend accepts it; the run is
  // tracked by the progress bar in 巡检任务, not by this banner.
  assert.match(callSite("onCreated={"), /notify\(`巡检任务[^`]*`, "success"\)/);
});

test("a probe that is still running stays until its result replaces it", () => {
  assert.match(callSite("const testConnection ="), /notify\(`正在测试[^`]*`, "progress"\)/);
});

test("a partly failed batch is a warning and a clean batch is a success", () => {
  const backup = app.slice(app.indexOf("const createBackup ="), app.indexOf("const clearAuthenticatedState"));
  assert.match(backup, /notify\("请先选择要备份的设备", "warning"\)/);
  assert.match(backup, /failures \? "warning" : "success"/);
});

test("every notice reaches the banner through the tone-carrying helper", () => {
  // ResultsPage callbacks pass through notify only while the session that
  // rendered the page is still current.
  assert.match(app, /onNotice=\{\(message, tone\) => \{ if \(operationEpochRef\.current\.isCurrentSession\(renderedSessionGeneration\)\) notify\(message, tone\); \}\}/);
  // The raw setter is only for clearing, never for announcing.
  for (const call of app.match(/setNotice\([^)]*/g) ?? []) {
    assert.ok(
      call === "setNotice(null" || call.startsWith("setNotice({ message, tone }"),
      `setNotice must not be called with a bare message: ${call}`,
    );
  }
});

test("every tone renders with its own colour and an icon", () => {
  for (const tone of ["success", "warning", "progress"]) {
    assert.match(css, new RegExp(`\\.notice--${tone}\\s*\\{`), `missing .notice--${tone} style`);
  }
  assert.match(app, /notice__icon/, "the banner must render a leading status icon");
});

test("the banner schedules and clears the dismiss timer", () => {
  // Without cleanup a pending timer from a previous message can wipe the next
  // one early, so the effect must both key off the message and clear itself.
  const banner = app.slice(app.indexOf("function NoticeBanner"), app.indexOf("function Dashboard"));
  assert.match(banner, /useEffect\(/, "the banner must schedule its own dismissal");
  assert.match(banner, /noticeAutoDismissMs\(/, "the delay must come from the shared rule");
  assert.match(banner, /clearTimeout/, "a pending timer must be cleared when the message changes");
});
