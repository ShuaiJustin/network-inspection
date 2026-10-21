import assert from "node:assert/strict";
import test from "node:test";

const inspectionItemsModule = await import("../src/app/inspection-items.js").catch(() => null);

test("inspection items are normalized for the result details table", () => {
  assert.equal(typeof inspectionItemsModule?.inspectionItemRows, "function");

  const rows = inspectionItemsModule.inspectionItemRows({
    inspection_items: {
      cpu: {
        conclusion: "warning",
        source: "SSH",
        adapter_version: "builtin-ssh-v1",
        reference: "display cpu-usage",
        downgrade_reason: "SNMP value unavailable",
        value: 82,
      },
    },
  });

  assert.deepEqual(rows, [{
    id: "cpu",
    conclusion: "warning",
    source: "SSH",
    adapterVersion: "builtin-ssh-v1",
    reference: "display cpu-usage",
    downgradeReason: "SNMP value unavailable",
    value: 82,
  }]);
});

test("hardware, temperature, alarms and CF/Flash values have readable Chinese summaries", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  assert.equal(typeof format, "function");

  assert.equal(format("active_alarms", { total: 2, critical: 1, warning: 1 }), "共 2 条，严重 1 条，警告 1 条");
  assert.equal(format("fans", {
    total: 2,
    abnormal: 1,
    components: [{ id: "FAN1", status: "normal" }, { id: "FAN2", status: "abnormal" }],
  }), "共 2 个，异常 1 个；FAN1：正常；FAN2：异常");
  assert.equal(format("temperature", {
    sensors: 3,
    alarming: 0,
    max_current: 38,
    readings: [{ name: "LPU/0", current: 38, unit: "C", status: "normal" }],
  }), "共 3 个传感器，最高 38°C；LPU/0：38°C（正常）");
  assert.equal(format("storage", {
    used_percent: 75,
    filesystems: [{ name: "flash:", media_type: "flash", used_percent: 75, access: "rw", status: "normal" }],
  }), "flash:（Flash）：已用 75%，可读写，正常");
});

test("inspection summaries include actionable hardware details and capacity", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  const alarmText = format("active_alarms", {
    total: 1,
    critical: 1,
    warning: 0,
    alarms: [{ name: "hwPowerRemove", severity: "critical", description: "Power supply removed" }],
  });
  assert.match(alarmText, /hwPowerRemove/);
  assert.match(alarmText, /Power supply removed/);

  const fanText = format("fans", {
    total: 1,
    abnormal: 1,
    components: [{
      id: "FAN1",
      model: "FAN-028A",
      status: "abnormal",
      alarms: [{ description: "Fan stopped" }],
    }],
  });
  assert.match(fanText, /FAN-028A/);
  assert.match(fanText, /Fan stopped/);

  const temperatureText = format("temperature", {
    sensors: 1,
    alarming: 0,
    max_current: 38,
    readings: [{
      name: "LPU\/0",
      current: 38,
      unit: "C",
      status: "normal",
      thresholds: { minor: 68, major: 76, fatal: 83 },
    }],
  });
  assert.match(temperatureText, /68/);
  assert.match(temperatureText, /76/);
  assert.match(temperatureText, /83/);

  const storageText = format("storage", {
    used_percent: 75,
    filesystems: [{
      name: "flash:",
      media_type: "flash",
      total_bytes: 1073741824,
      free_bytes: 268435456,
      used_percent: 75,
      access: "rw",
      status: "normal",
    }],
  });
  assert.match(storageText, /1 GB/);
  assert.match(storageText, /256 MB/);
});

test("capacity-only storage output does not claim an unknown health state", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  const storageText = format("storage", {
    used_percent: 60,
    filesystems: [{
      name: "hda1:",
      media_type: "hda",
      total_bytes: 1172 * 1024 ** 2,
      free_bytes: 464 * 1024 ** 2,
      used_percent: 60,
    }],
  });

  assert.match(storageText, /已用 60%/);
  assert.match(storageText, /空闲 464 MB/);
  assert.doesNotMatch(storageText, /读写状态未知|状态未知/);
});

test("an explicitly disabled NTP service is shown as not configured", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  assert.equal(format("ntp", { configured: false, synchronized: null }), "未配置");
});

test("compatibility and per-entity diagnostics have readable summaries", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;

  assert.match(format("device_information", {
    hostname: "Access-SW",
    model: "S5735-L24T4S-A1",
    software_version: "V200R022C00SPC500",
    adapter_compatibility: { status: "unverified" },
  }), /型号\/版本未验证/);
  assert.match(format("cpu", {
    percent: 99,
    readings: [
      { object_id: "slot 1", percent: 5 },
      { object_id: "slot 2", percent: 6 },
      { object_id: "slot 3", percent: 7 },
      { object_id: "system", scope: "management-plane", percent: 99 },
    ],
  }), /整机\/管理平面：99%/);
  const interfaces = format("interfaces", {
    interfaces_total: 4,
    interfaces_up: 1,
    interfaces_down: 3,
    interfaces_admin_down: 1,
    interfaces_error_disabled: 1,
    interfaces_unknown: 1,
    interface_details: [{
      name: "GE1/0/2", physical_status: "down", admin_down: false, error_disabled: true,
    }],
  });
  assert.match(interfaces, /管理关闭 1/);
  assert.match(interfaces, /错误禁用 1/);
  assert.match(interfaces, /未知 1/);
  assert.match(interfaces, /GE1\/0\/2：错误禁用/);
  assert.match(format("interface_errors", {
    total: 123,
    discarded: 7,
    interfaces: [
      { name: "GE1\/0\/1", total_errors: 0 },
      { name: "GE1\/0\/2", total_errors: 0 },
      { name: "GE1\/0\/3", total_errors: 0 },
      { name: "GE1\/0\/4", total_errors: 123, crc_errors: 123 },
    ],
  }), /总错误 123，丢弃 7；GE1\/0\/4：总错误 123，CRC 123/);
});

test("resource details lead with the same sampling window used by the summary", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  const text = format("cpu", {
    percent: 99,
    readings: [
      { object_id: "slot:1", scope: "overall", window: "5s", percent: 5 },
      { object_id: "slot:1", scope: "overall", window: "1m", percent: 100 },
      { object_id: "slot:2", scope: "overall", window: "5s", percent: 99 },
    ],
  });

  assert.match(text, /^99%；slot:2：99%（5 秒）/);
});

test("unqualified interface discards are prioritized and displayed", () => {
  const format = inspectionItemsModule.formatInspectionItemValue;
  const text = format("interface_errors", {
    total: 0,
    discarded: 100,
    interfaces: [
      { name: "A", total_errors: 0, discards: 100 },
      { name: "B", total_errors: 1 },
      { name: "C", total_errors: 1 },
      { name: "D", total_errors: 1 },
    ],
  });

  assert.match(text, /^总错误 0，丢弃 100；A：总错误 0，丢弃 100/);
});

const { formatInspectionItemValue, inspectionReferenceText } = inspectionItemsModule ?? {};

test("link aggregation, neighbors and STP values read as sentences, not raw keys", () => {
  assert.equal(
    formatInspectionItemValue("link_aggregation", { groups: 2, degraded: 1, details: [
      { name: "Eth-Trunk1", status: "up", down_members: ["GE0/0/2"], degraded: true },
      { name: "Eth-Trunk2", status: "up", down_members: [], degraded: false },
    ] }),
    "共 2 组，异常 1 组；Eth-Trunk1：状态 up，异常成员 GE0/0/2",
  );
  assert.equal(formatInspectionItemValue("link_aggregation", { groups: 0, degraded: 0 }), "共 0 组，异常 0 组");
  assert.equal(formatInspectionItemValue("routing_neighbors", { total: 3, established: 2 }), "共 3 个邻居，已建立 2 个");
  assert.equal(
    formatInspectionItemValue("stp", { stp_discarding_ports: 2, stp_broken_ports: 0 }),
    "阻塞端口 2 个，故障端口 0 个",
  );
});

test("references that mention errors are shown verbatim", () => {
  assert.equal(inspectionReferenceText("IF-MIB::ifInErrors/ifOutErrors"), "IF-MIB::ifInErrors/ifOutErrors");
  assert.equal(inspectionReferenceText("show interfaces counters errors"), "show interfaces counters errors");
  assert.equal(inspectionReferenceText("vendor CPU metric"), "厂商 CPU 指标");
  assert.equal(inspectionReferenceText(null), "-");
});
