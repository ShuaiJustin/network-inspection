import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");

const rulesFor = (selector) =>
  [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, head]) => head.split(",").some((part) => part.trim() === selector))
    .map(([, , body]) => body);

const ruleFor = (selector) => {
  const [body] = rulesFor(selector);
  assert.ok(body, `expected a rule for ${selector}`);
  return body;
};

test("the shell fills the viewport instead of growing with the page", () => {
  // With a page-tall shell the window itself scrolls, which drags the sidebar
  // and the top bar out of view on long pages such as the log list.
  assert.match(ruleFor(".app-shell"), /height:\s*100vh/);
  assert.match(ruleFor(".app-shell"), /overflow:\s*hidden/);
});

test("the sidebar keeps its own height and scrolls on its own", () => {
  const sidebar = ruleFor(".sidebar");
  assert.match(sidebar, /flex:\s*0 0 228px/, "the sidebar must not shrink with the content");
  assert.match(sidebar, /overflow-y:\s*auto/, "a short window scrolls the nav, not the page");
});

test("only the content column scrolls", () => {
  const panel = ruleFor(".main-panel");
  assert.match(panel, /flex-direction:\s*column/);
  assert.match(panel, /overflow:\s*hidden/);
  assert.match(ruleFor(".topbar"), /flex-shrink:\s*0/, "the top bar stays pinned above the content");
  assert.match(ruleFor(".content"), /overflow-y:\s*auto/);
});

test("long operator errors wrap instead of widening the minimum desktop layout", () => {
  assert.match(ruleFor(".notice__body > span:last-child"), /overflow-wrap:\s*anywhere/);
  assert.match(ruleFor(".dialog-error"), /overflow-wrap:\s*anywhere/);
  assert.match(ruleFor(".inline-error"), /overflow-wrap:\s*anywhere/);
  assert.match(ruleFor(".page-heading h1"), /overflow-wrap:\s*anywhere/);
  assert.match(ruleFor(".result-detail__heading h2"), /overflow-wrap:\s*anywhere/);
});
