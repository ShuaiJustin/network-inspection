import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";
import {
  RESULT_FILTERS,
  PRIMARY_RESULT_FILTERS,
  filterResults,
  formatMetric,
  hasUncoveredInspectionItems,
  keepSelectionVisible,
  primaryInspectionFinding,
  resultFilterCount,
  sortResultsByAttention,
  summarizeResults,
  taskProgressPercent,
} from "../src/app/results-view.js";

test("primary result filters match the operator's main decisions", () => {
  assert.deepEqual(PRIMARY_RESULT_FILTERS.map((filter) => filter.id), ["all", "attention", "failed", "normal"]);
  const results = [
    { id: 1, conclusion: "critical" },
    { id: 2, conclusion: "warning" },
    { id: 3, conclusion: "connection_failed" },
    { id: 4, conclusion: "authentication_failed" },
    { id: 5, conclusion: "data_fetch_failed" },
    { id: 6, conclusion: "normal" },
  ];
  assert.deepEqual(filterResults(results, "attention").map((result) => result.id), [1, 2]);
  assert.deepEqual(filterResults(results, "failed").map((result) => result.id), [3, 4, 5]);
  assert.equal(resultFilterCount(results, "attention"), 2);
  assert.equal(resultFilterCount(results, "failed"), 3);
});

test("result rows are sorted by severity without mutating the backend order", () => {
  const results = [
    { id: 1, conclusion: "normal" },
    { id: 2, conclusion: "connection_failed" },
    { id: 3, conclusion: "warning" },
    { id: 4, conclusion: "critical" },
  ];

  assert.deepEqual(sortResultsByAttention(results).map((result) => result.id), [4, 3, 2, 1]);
  assert.deepEqual(results.map((result) => result.id), [1, 2, 3, 4]);
});

test("the primary finding prefers a critical check over an earlier warning", () => {
  const finding = primaryInspectionFinding({
    metrics: {
      inspection_items: {
        memory: { conclusion: "warning", value: 84 },
        cpu: { conclusion: "critical", value: 97 },
      },
    },
  });

  assert.deepEqual(finding, { id: "cpu", conclusion: "critical", value: 97 });
  assert.equal(primaryInspectionFinding({ metrics: {} }), null);
});

test("the summary counts every conclusion the backend can return", () => {
  const summary = summarizeResults([
    { conclusion: "normal" },
    { conclusion: "normal" },
    { conclusion: "warning" },
    { conclusion: "critical" },
    { conclusion: "connection_failed" },
    { conclusion: "authentication_failed" },
    { conclusion: "data_fetch_failed" },
    { conclusion: "unsupported" },
    { conclusion: "not_applicable" },
    { conclusion: "cancelled" },
  ]);

  assert.equal(summary.total, 10);
  assert.equal(summary.normal, 2);
  // The card groups warning and critical, exactly like the design.
  assert.equal(summary.attention, 2);
  // Connection and authentication failures share one card.
  assert.equal(summary.unreachable, 2);
  assert.equal(summary.dataFetchFailed, 1);
  assert.equal(summary.failed, 3, "all collection failures must be visible in the overview");
  assert.equal(summary.uncovered, 2, "unsupported and not-applicable devices need their own count");
  assert.equal(summary.cancelled, 1);
});

test("an empty result set summarizes to zeroes rather than NaN", () => {
  const summary = summarizeResults([]);
  assert.deepEqual(
    [summary.total, summary.normal, summary.attention, summary.unreachable],
    [0, 0, 0, 0],
  );
});

test("the summary exposes devices with unsupported inspection items", () => {
  const results = [
    {
      id: 1,
      conclusion: "normal",
      metrics: {
        inspection_items: {
          cpu: { conclusion: "normal" },
          temperature: { conclusion: "unsupported" },
        },
      },
    },
    {
      id: 2,
      conclusion: "normal",
      metrics: {
        inspection_items: {
          stp: { conclusion: "not_applicable" },
        },
      },
    },
  ];

  assert.equal(hasUncoveredInspectionItems(results[0]), true);
  assert.equal(hasUncoveredInspectionItems(results[1]), false, "not-applicable checks are intentionally skipped");
  assert.equal(summarizeResults(results).devicesWithUncoveredItems, 1);
});

test("every filter chip maps to the conclusions it claims", () => {
  const results = [
    { id: 1, conclusion: "normal" },
    { id: 2, conclusion: "warning" },
    { id: 3, conclusion: "critical" },
    { id: 4, conclusion: "connection_failed" },
    { id: 5, conclusion: "authentication_failed" },
    { id: 6, conclusion: "data_fetch_failed" },
    { id: 7, conclusion: "unsupported" },
    { id: 8, conclusion: "not_applicable" },
    { id: 9, conclusion: "cancelled" },
    {
      id: 10,
      conclusion: "normal",
      metrics: { inspection_items: { temperature: { conclusion: "unsupported" } } },
    },
  ];

  assert.equal(filterResults(results, "all").length, 10);
  assert.deepEqual(filterResults(results, "normal").map((r) => r.id), [1, 10]);
  assert.deepEqual(filterResults(results, "warning").map((r) => r.id), [2]);
  assert.deepEqual(filterResults(results, "critical").map((r) => r.id), [3]);
  assert.deepEqual(filterResults(results, "connection_failed").map((r) => r.id), [4]);
  assert.deepEqual(filterResults(results, "authentication_failed").map((r) => r.id), [5]);
  assert.deepEqual(filterResults(results, "data_fetch_failed").map((r) => r.id), [6]);
  assert.deepEqual(filterResults(results, "unsupported").map((r) => r.id), [7]);
  assert.deepEqual(filterResults(results, "not_applicable").map((r) => r.id), [8]);
  assert.deepEqual(filterResults(results, "cancelled").map((r) => r.id), [9]);
  assert.deepEqual(filterResults(results, "item_coverage_gap").map((r) => r.id), [10]);
});

test("the filter chips cover the whole toolbar shown in the design", () => {
  assert.deepEqual(
    RESULT_FILTERS.map((filter) => filter.id),
    ["all", "normal", "warning", "critical", "connection_failed", "authentication_failed", "data_fetch_failed", "unsupported", "not_applicable", "item_coverage_gap", "cancelled"],
  );
  for (const filter of RESULT_FILTERS) {
    assert.ok(filter.label && !/[a-z_]/.test(filter.label), `${filter.id} needs a Chinese label`);
  }
});

test("a device that never answered shows a dash instead of a fabricated zero", () => {
  // A connection failure leaves the metrics empty. Rendering 0% would claim the
  // device reported an idle CPU.
  assert.equal(formatMetric(undefined, "%"), "-");
  assert.equal(formatMetric(null, "%"), "-");
  assert.equal(formatMetric(0, "%"), "0%");
  assert.equal(formatMetric(42, "%"), "42%");
  assert.equal(formatMetric("187天14小时"), "187天14小时");
});

test("interface up/down is only rendered when the interface walk succeeded", () => {
  assert.equal(formatMetric(undefined), "-");
});

test("progress is a whole percentage that never exceeds 100", () => {
  assert.equal(taskProgressPercent({ completed_devices: 2, total_devices: 5 }), 40);
  assert.equal(taskProgressPercent({ completed_devices: 0, total_devices: 5 }), 0);
  assert.equal(taskProgressPercent({ completed_devices: 5, total_devices: 5 }), 100);
  // A task with no devices must not divide by zero.
  assert.equal(taskProgressPercent({ completed_devices: 0, total_devices: 0 }), 0);
  assert.equal(taskProgressPercent({ completed_devices: 7, total_devices: 5 }), 100);
});

test("the task progress bar is styled, not a bare full-width block", () => {
  const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const ruleFor = (selector) => {
    const found = rules.filter(([, head]) => head.split(",").some((p) => p.trim() === selector));
    assert.ok(found.length, `expected a rule for ${selector}`);
    return found.map(([, , body]) => body).join(" ");
  };

  const track = ruleFor(".task-progress");
  assert.match(track, /border-radius/, "the track needs rounded ends, not a square slab");
  assert.match(track, /height:\s*6px/, "the design uses a slim bar");
  assert.match(track, /overflow:\s*hidden/, "the fill must be clipped to the rounded track");

  const fill = ruleFor(".task-progress__fill");
  assert.match(fill, /transition/, "progress should animate rather than jump");
});

test("a selection hidden by the active filter is dropped", () => {
  // Otherwise the detail/screenshot panel keeps describing a device that is no
  // longer in the table, which is what the filtered screenshot exposed.
  const visible = [{ id: 5 }, { id: 6 }];
  assert.equal(keepSelectionVisible(3, visible), null);
});

test("a selection that is still visible survives filtering", () => {
  assert.equal(keepSelectionVisible(6, [{ id: 5 }, { id: 6 }]), 6);
});

test("no selection stays no selection", () => {
  assert.equal(keepSelectionVisible(null, [{ id: 5 }]), null);
});

test("the detail heading keeps its status pill from stretching", () => {
  const css = readFileSync(new URL("../src/styles/index.css", import.meta.url), "utf8");
  const rules = [...css.matchAll(/([^{}]+)\{([^{}]*)\}/g)];
  const body = rules
    .filter(([, head]) => head.split(",").some((p) => p.trim() === ".result-detail__heading"))
    .map(([, , b]) => b)
    .join(" ");
  assert.ok(body, "expected a .result-detail__heading rule");
  assert.match(body, /align-items:\s*flex-start/, "a stretched pill renders as a tall block");
});
