import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import {
  deviceTypeOptions,
  vendorText,
  platformOptions,
  vendorOptions,
} from "../src/app/device-catalog.js";

const catalog = {
  device_types: [
    { id: "switch", label: "交换机" },
    { id: "firewall", label: "防火墙" },
  ],
  vendors: [
    {
      id: "Huawei",
      label: "华为",
      default_platform_id: "huawei-vrp",
      device_types: ["switch", "firewall"],
      platforms: [
        { id: "huawei-vrp", label: "VRP", device_types: ["switch", "firewall"] },
      ],
    },
  ],
};

test("catalog helpers derive every device form option from backend data", () => {
  assert.deepEqual(vendorOptions(catalog), [{ value: "Huawei", label: "华为" }]);
  assert.deepEqual(deviceTypeOptions(catalog, "Huawei"), [
    { value: "switch", label: "交换机" },
    { value: "firewall", label: "防火墙" },
  ]);
  assert.deepEqual(platformOptions(catalog, "Huawei"), [
    { value: "huawei-vrp", label: "VRP" },
  ]);
});

test("vendor ids are rendered with the catalog's Chinese label", () => {
  assert.equal(vendorText(catalog, "Huawei"), "华为");
  assert.equal(vendorText(catalog, "Unknown"), "Unknown");
});

test("App loads the device catalog and does not embed a vendor option array", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");

  assert.match(app, /api<DeviceCatalog>\("\/catalog\/devices"\)/);
  assert.equal(/\["Huawei",\s*"H3C",\s*"Ruijie",\s*"Cisco"\]/.test(app), false);
  assert.match(app, /name="platform_id"/);
});

test("device search and sorting use visible catalog labels with accessible headers", () => {
  const app = readFileSync(new URL("../src/app/App.tsx", import.meta.url), "utf8");
  const search = app.slice(app.indexOf("const filteredDevices"), app.indexOf("const selectTask"));
  const devicesPage = app.slice(app.indexOf("function DevicesPage"), app.indexOf("function TasksPage"));

  assert.match(search, /vendorText\(catalog, device\.vendor\)/);
  assert.match(search, /deviceTypeText\(catalog, device\.device_type\)/);
  assert.match(devicesPage, /sortField === "vendor"[\s\S]*vendorText\(catalog, device\.vendor\)/);
  assert.match(devicesPage, /sortField === "device_type"[\s\S]*deviceTypeText\(catalog, device\.device_type\)/);
  assert.match(devicesPage, /<th aria-sort=/);
  assert.match(devicesPage, /"ascending" : "descending"/);
});
