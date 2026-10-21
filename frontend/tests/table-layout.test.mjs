import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

const rulesFor = (selector) =>
  [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)]
    .filter(([, head]) => head.split(",").some((part) => part.trim() === selector))
    .map(([, , body]) => body);

test("action cells stay table cells", () => {
  // `<td className="row-actions">` is used by the device and task tables. Giving
  // that class `display: inline-flex` rips the cell out of the table layout: it
  // stops honouring the column width its own `<th>` reserved, loses the row
  // height, and draws a short bottom border that does not reach the row edge.
  assert.ok(app.includes('<td className="row-actions">'), "tables still mark cells with row-actions");
  for (const body of rulesFor(".row-actions")) {
    assert.doesNotMatch(body, /display:\s*(inline-)?flex/, "row-actions must not force a flex box onto <td>");
  }
});

test("toolbar trailing action stays right aligned when the row wraps", () => {
  // `.device-toolbar` wraps below ~1280px. Without `margin-left: auto` on the
  // trailing button, space-between drops it to the far left of the second line.
  const [toolbarAction] = rulesFor(".device-toolbar > [data-slot=\"button\"]");
  assert.ok(toolbarAction, "expected a rule for the toolbar's trailing button");
  assert.match(toolbarAction, /margin-left:\s*auto/);
});

test("toolbar search box matches the filter select metrics", () => {
  const [searchBox] = rulesFor(".search-box");
  const [filterSelect] = rulesFor(".device-filters select");
  const height = (body) => body.match(/height:\s*([\w.]+)/)?.[1];
  assert.equal(height(searchBox), height(filterSelect), "search box and selects must share one height");
  assert.match(searchBox, /border:\s*1px solid var\(--c-border\)/);
});

test("file-picker label matches the button height scale", () => {
  // Buttons size with tailwind's `h-9` (2.25rem). theme.css pins the root font
  // size at 13px, so a hardcoded px height drifts out of line with them.
  const [buttonLabel] = rulesFor(".button-label");
  assert.match(buttonLabel, /height:\s*2\.25rem/);
});

test("backup and adapter actions remain visible at a 1280px desktop width", () => {
  const [backupTable] = rulesFor(".backup-history-table");
  const [adapterTable] = rulesFor(".adapter-table");

  assert.match(backupTable, /min-width:\s*980px/);
  assert.match(backupTable, /table-layout:\s*fixed/);
  assert.match(adapterTable, /min-width:\s*980px/);
  assert.match(adapterTable, /table-layout:\s*fixed/);
});
