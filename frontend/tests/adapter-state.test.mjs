import assert from "node:assert/strict";
import test from "node:test";

const adapterStateModule = await import("../src/app/adapter-state.js").catch(() => null);

test("adapter catalog rows only retain verified records", () => {
  assert.equal(typeof adapterStateModule?.verifiedAdapterRows, "function");

  assert.deepEqual(adapterStateModule.verifiedAdapterRows([
    { vendor: "Huawei", status: "verified", model_range: "S12700", version_range: "V200R", inspection_items: ["cpu"] },
    { vendor: "Cisco", status: "pending", model_range: "C9300", version_range: "17.x", inspection_items: ["cpu"] },
  ]), [{
    vendor: "Huawei",
    modelRange: "S12700",
    versionRange: "V200R",
    inspectionItems: ["cpu"],
  }]);
});

test("implemented adapter capabilities remain explicitly unverified", () => {
  assert.equal(typeof adapterStateModule?.adapterCapabilityRows, "function");

  assert.deepEqual(adapterStateModule.adapterCapabilityRows([{
    vendor: "Huawei",
    platform_id: "huawei-vrp",
    platform_label: "VRP",
    status: "unverified",
    device_types: ["switch", "router", "firewall"],
    protocols: ["SNMP", "SSH"],
    backup_command: "display current-configuration",
    inspection_items: ["cpu", "memory"],
  }]), [{
    vendor: "Huawei",
    platformId: "huawei-vrp",
    platformLabel: "VRP",
    status: "unverified",
    deviceTypes: ["switch", "router", "firewall"],
    protocols: ["SNMP", "SSH"],
    backupCommand: "display current-configuration",
    inspectionItems: ["cpu", "memory"],
  }]);
});

test("adapter rows can be searched across their visible fields", () => {
  assert.equal(typeof adapterStateModule?.filterAdapterRows, "function");
  const rows = [
    { vendor: "Huawei", platformLabel: "VRP", inspectionItems: ["cpu"] },
    { vendor: "H3C", platformLabel: "Comware", inspectionItems: ["memory"] },
  ];

  assert.deepEqual(adapterStateModule.filterAdapterRows(rows, "", "comware"), [rows[1]]);
  assert.deepEqual(adapterStateModule.filterAdapterRows(rows, "Huawei", "CPU"), [rows[0]]);
});
