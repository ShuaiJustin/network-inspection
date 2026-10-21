import assert from "node:assert/strict";
import test from "node:test";

const thresholdSettingsModule = await import("../src/app/threshold-settings.js").catch(() => null);

test("threshold settings reject a critical value below its warning value", () => {
  assert.equal(typeof thresholdSettingsModule?.validateThresholdRanges, "function");

  assert.equal(thresholdSettingsModule.validateThresholdRanges({
    cpu_warning_threshold: 80,
    cpu_critical_threshold: 95,
    memory_warning_threshold: 80,
    memory_critical_threshold: 95,
    storage_warning_threshold: 80,
    storage_critical_threshold: 95,
  }), null);

  assert.equal(thresholdSettingsModule.validateThresholdRanges({
    cpu_warning_threshold: 96,
    cpu_critical_threshold: 95,
    memory_warning_threshold: 80,
    memory_critical_threshold: 95,
    storage_warning_threshold: 80,
    storage_critical_threshold: 95,
  }), "CPU 严重阈值不能低于警告阈值");
});
