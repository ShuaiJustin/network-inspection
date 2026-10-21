export function inspectionItemRows(metrics) {
  const items = metrics?.inspection_items;
  if (!items || typeof items !== "object" || Array.isArray(items)) return [];
  return Object.entries(items)
    .filter(([, item]) => item && typeof item === "object" && !Array.isArray(item))
    .map(([id, item]) => ({
      id,
      conclusion: String(item.conclusion ?? "unsupported"),
      source: String(item.source ?? "unsupported"),
      adapterVersion: String(item.adapter_version ?? "-"),
      reference: String(item.reference ?? "-"),
      downgradeReason: item.downgrade_reason == null ? null : String(item.downgrade_reason),
      value: item.value,
    }));
}

const STATUS_TEXT = {
  normal: "正常",
  abnormal: "异常",
  unknown: "状态未知",
  read_only: "只读",
  fault: "故障",
};

const MEDIA_TYPE_TEXT = {
  cf: "CF 卡",
  flash: "Flash",
  bootflash: "Bootflash",
  hda: "HDA",
  storage: "存储",
};

function limitedDetails(values, format, limit = 3) {
  const visible = values.slice(0, limit).map(format);
  if (values.length > limit) visible.push(`另有 ${values.length - limit} 项`);
  return visible.join("；");
}

const RESOURCE_SCOPE_TEXT = {
  overall: "整体",
  "management-plane": "管理平面",
  "data-plane": "数据平面",
  container: "容器",
};

function resourceReadingLabel(reading) {
  const objectId = reading?.object_id === "system" ? "整机" : reading?.object_id;
  const scope = reading?.scope && reading.scope !== "overall"
    ? (RESOURCE_SCOPE_TEXT[reading.scope] ?? reading.scope)
    : null;
  return [objectId, scope].filter(Boolean).join("/") || RESOURCE_SCOPE_TEXT[reading?.scope] || "-";
}

function prioritizedResourceReadings(readings) {
  const windowOrder = ["5s", "current", "1m", "5m"];
  const hasOverall = readings.some((reading) => reading?.scope === "overall");
  const candidates = hasOverall
    ? readings.filter((reading) => reading?.scope === "overall")
    : readings;
  const selectedWindow = windowOrder.find((window) => (
    candidates.some((reading) => reading?.window === window)
  )) ?? candidates[0]?.window;

  const group = (reading) => {
    const candidateScope = !hasOverall || reading?.scope === "overall";
    if (candidateScope && reading?.window === selectedWindow) return 0;
    if (reading?.window === selectedWindow) return 1;
    const windowRank = windowOrder.indexOf(reading?.window);
    return 2 + (windowRank === -1 ? windowOrder.length : windowRank);
  };
  return [...readings].sort((left, right) => {
    const groupDifference = group(left) - group(right);
    if (groupDifference) return groupDifference;
    const leftValue = typeof left?.percent === "number" ? left.percent : Number.NEGATIVE_INFINITY;
    const rightValue = typeof right?.percent === "number" ? right.percent : Number.NEGATIVE_INFINITY;
    return rightValue - leftValue;
  });
}

function formatBytes(value) {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return null;
  const units = ["B", "KB", "MB", "GB", "TB"];
  let amount = value;
  let unit = 0;
  while (amount >= 1024 && unit < units.length - 1) {
    amount /= 1024;
    unit += 1;
  }
  const displayed = amount >= 10 || Number.isInteger(amount) ? Math.round(amount) : amount.toFixed(1);
  return `${displayed} ${units[unit]}`;
}

function alarmDescription(alarm) {
  return [alarm?.name, alarm?.severity, alarm?.description].filter(Boolean).join(" / ");
}

function thresholdText(thresholds) {
  if (!thresholds || typeof thresholds !== "object") return "";
  const labels = { low: "低", lower: "下限", minor: "次要", major: "主要", fatal: "严重", high: "高", upper: "上限" };
  const values = Object.entries(thresholds).map(([key, value]) => `${labels[key] ?? key} ${value}°C`);
  return values.length ? `，阈值 ${values.join("/")}` : "";
}

export function formatInspectionItemValue(id, value) {
  if (value === null || value === undefined || value === "") return "-";
  if (typeof value === "number") return ["cpu", "memory"].includes(id) ? `${value}%` : String(value);
  if (typeof value === "string") return value;
  if (typeof value !== "object" || Array.isArray(value)) return String(value);

  if (["cpu", "memory"].includes(id) && "percent" in value) {
    const prefix = value.percent == null ? "-" : `${value.percent}%`;
    const readings = Array.isArray(value.readings) ? value.readings : [];
    if (!readings.length) return prefix;
    const prioritized = prioritizedResourceReadings(readings);
    return `${prefix}；${limitedDetails(prioritized, (reading) => {
      const label = resourceReadingLabel(reading);
      const windowText = ({ current: "当前", "5s": "5 秒", "1m": "1 分钟", "5m": "5 分钟" })[reading.window] ?? reading.window;
      const window = windowText ? `（${windowText}）` : "";
      return `${label}：${reading.percent ?? "-"}%${window}`;
    })}`;
  }

  if (id === "active_alarms") {
    const prefix = `共 ${value.total ?? 0} 条，严重 ${value.critical ?? 0} 条，警告 ${value.warning ?? 0} 条`;
    const alarms = Array.isArray(value.alarms) ? value.alarms : [];
    return alarms.length ? `${prefix}；${limitedDetails(alarms, alarmDescription)}` : prefix;
  }
  if (["boards", "power_supplies", "fans"].includes(id)) {
    const prefix = `共 ${value.total ?? 0} 个，异常 ${value.abnormal ?? 0} 个`;
    const components = Array.isArray(value.components) ? value.components : [];
    const details = [];
    if (components.length) details.push(limitedDetails(components, (component) => {
      const model = component.model && component.model !== "-" ? ` / ${component.model}` : "";
      const alarms = Array.isArray(component.alarms) ? component.alarms : [];
      const alarm = alarms.length ? ` / ${limitedDetails(alarms, alarmDescription, 1)}` : "";
      return `${component.id ?? "-"}${model}：${STATUS_TEXT[component.status] ?? component.status ?? "状态未知"}${alarm}`;
    }));
    const alarms = Array.isArray(value.alarms) ? value.alarms : [];
    if (alarms.length) details.push(`告警 ${limitedDetails(alarms, alarmDescription, 2)}`);
    return details.length ? `${prefix}；${details.join("；")}` : prefix;
  }
  if (id === "temperature") {
    const maximum = value.max_current == null ? "" : `，最高 ${value.max_current}°C`;
    const prefix = `共 ${value.sensors ?? 0} 个传感器${maximum}`;
    const readings = Array.isArray(value.readings) ? value.readings : [];
    if (!readings.length) return prefix;
    return `${prefix}；${limitedDetails(readings, (reading) => `${reading.name ?? "-"}：${reading.current ?? "-"}°C（${STATUS_TEXT[reading.status] ?? reading.status ?? "状态未知"}${thresholdText(reading.thresholds)}）`)}`;
  }
  if (id === "storage") {
    const filesystems = Array.isArray(value.filesystems) ? value.filesystems : [];
    if (!filesystems.length) return value.used_percent == null ? "-" : `已用 ${value.used_percent}%`;
    return limitedDetails(filesystems, (filesystem) => {
      const media = MEDIA_TYPE_TEXT[filesystem.media_type] ?? filesystem.media_type ?? "存储";
      const access = filesystem.access === "rw" ? "可读写" : filesystem.access === "ro" ? "只读" : null;
      const status = filesystem.status && filesystem.status !== "unknown"
        ? (STATUS_TEXT[filesystem.status] ?? filesystem.status)
        : null;
      const total = formatBytes(filesystem.total_bytes);
      const free = formatBytes(filesystem.free_bytes);
      const capacity = total || free ? `，总量 ${total ?? "-"}，空闲 ${free ?? "-"}` : "";
      const verifiedState = [access, status].filter(Boolean);
      const stateText = verifiedState.length ? `，${verifiedState.join("，")}` : "";
      return `${filesystem.name ?? "-"}（${media}）：已用 ${filesystem.used_percent ?? "-"}%${capacity}${stateText}`;
    });
  }
  if (id === "device_information") {
    const identity = [value.hostname, value.model, value.software_version].filter(Boolean).join(" / ") || "-";
    const compatibility = value.adapter_compatibility;
    if (!compatibility || typeof compatibility !== "object") return identity;
    if (compatibility.status === "verified") {
      const evidence = [compatibility.adapter_version, compatibility.verification_date].filter(Boolean).join("，");
      return `${identity}；适配已验证${evidence ? `（${evidence}）` : ""}`;
    }
    return `${identity}；型号/版本未验证`;
  }
  if (id === "interfaces") {
    const parts = [
      `总数 ${value.interfaces_total ?? "-"}`,
      `Up ${value.interfaces_up ?? "-"}`,
      `Down ${value.interfaces_down ?? "-"}`,
    ];
    if (value.interfaces_admin_down != null) parts.push(`管理关闭 ${value.interfaces_admin_down}`);
    if (value.interfaces_error_disabled != null) parts.push(`错误禁用 ${value.interfaces_error_disabled}`);
    if (value.interfaces_unknown != null) parts.push(`未知 ${value.interfaces_unknown}`);
    const summary = parts.join("，");
    const details = Array.isArray(value.interface_details) ? value.interface_details : [];
    const stateText = (item) => {
      if (item.error_disabled) return "错误禁用";
      if (item.admin_down || item.admin_status === "down") return "管理关闭";
      const state = item.physical_status ?? item.oper_status ?? "unknown";
      return ({
        up: "正常", down: "Down", lowerLayerDown: "下层链路 Down", testing: "测试中",
        dormant: "休眠", notPresent: "未安装", unknown: "状态未知",
      })[state] ?? String(state);
    };
    const exceptional = details.filter((item) => stateText(item) !== "正常");
    return exceptional.length
      ? `${summary}；${limitedDetails(exceptional, (item) => `${item.name ?? "-"}：${stateText(item)}`)}`
      : summary;
  }
  if (id === "interface_errors" && "total" in value) {
    const discarded = value.discarded == null ? "" : `，丢弃 ${value.discarded}`;
    const prefix = `总错误 ${value.total ?? "-"}${discarded}`;
    const interfaces = Array.isArray(value.interfaces) ? value.interfaces : [];
    if (!interfaces.length) return prefix;
    const diagnosticScore = (item) => [
      item.total_errors, item.input_discards, item.output_discards, item.discards,
    ].reduce((total, count) => total + (typeof count === "number" ? count : 0), 0);
    const prioritized = [...interfaces].sort((left, right) => diagnosticScore(right) - diagnosticScore(left));
    return `${prefix}；${limitedDetails(prioritized, (item) => {
      const details = [`总错误 ${item.total_errors ?? "-"}`];
      if (item.crc_errors != null) details.push(`CRC ${item.crc_errors}`);
      if (item.input_discards != null || item.output_discards != null || item.discards != null) {
        details.push(`丢弃 ${(item.input_discards ?? 0) + (item.output_discards ?? 0) + (item.discards ?? 0)}`);
      }
      return `${item.name ?? "-"}：${details.join("，")}`;
    })}`;
  }
  if (id === "stp" && value.stp_enabled === false) return "未启用 STP";
  if (id === "stp" && ("stp_discarding_ports" in value || "stp_broken_ports" in value)) {
    return `阻塞端口 ${value.stp_discarding_ports ?? "-"} 个，故障端口 ${value.stp_broken_ports ?? 0} 个`;
  }
  if (id === "link_aggregation" && "groups" in value) {
    const prefix = `共 ${value.groups ?? 0} 组，异常 ${value.degraded ?? 0} 组`;
    const degraded = (Array.isArray(value.details) ? value.details : []).filter((group) => group?.degraded);
    if (!degraded.length) return prefix;
    return `${prefix}；${limitedDetails(degraded, (group) => {
      const members = Array.isArray(group.down_members) && group.down_members.length
        ? `，异常成员 ${group.down_members.join("、")}`
        : "";
      return `${group.name ?? "-"}：${group.status ? `状态 ${group.status}` : "状态异常"}${members}`;
    })}`;
  }
  if (id === "routing_neighbors" && "total" in value) {
    return `共 ${value.total ?? 0} 个邻居，已建立 ${value.established ?? 0} 个`;
  }
  if (id === "ntp" && value.configured === false) return "未配置";
  if (id === "ntp" && "synchronized" in value) return value.synchronized ? "已同步" : "未同步";
  if (id === "configuration_saved" && "saved" in value) return value.saved ? "已保存" : "存在未保存配置";
  return Object.entries(value).map(([key, item]) => `${key}: ${item}`).join("；") || "-";
}

// Check-item ids come straight from the backend definitions. The results table
// showed them verbatim, so a row was labelled "device_information" instead of
// naming the check in the language the rest of the table uses.
const ITEM_LABELS = {
  device_information: "设备信息",
  uptime: "运行时间",
  cpu: "CPU 利用率",
  memory: "内存利用率",
  interfaces: "接口概览",
  stp: "STP 状态",
  active_alarms: "当前告警",
  boards: "单板状态",
  power_supplies: "电源状态",
  fans: "风扇状态",
  temperature: "温度",
  interface_errors: "接口错误",
  link_aggregation: "链路聚合",
  routing_neighbors: "邻居状态",
  ntp: "NTP 同步",
  storage: "CF 卡/闪存存储",
  configuration_saved: "配置保存状态",
};

const REFERENCE_TEXT = {
  "vendor CPU metric": "厂商 CPU 指标",
  "vendor memory metric": "厂商内存指标",
  "vendor STP command or MIB": "厂商 STP 命令或 MIB",
  "vendor active alarm command or MIB": "厂商活动告警命令或 MIB",
  "vendor command or MIB": "厂商命令或 MIB",
  "ENTITY-MIB or vendor command": "ENTITY-MIB 或厂商命令",
  "ENTITY-SENSOR-MIB or vendor command": "ENTITY-SENSOR-MIB 或厂商命令",
};

// A reference is a command or OID name, not an error. Sending it through the
// error translator turned names such as "IF-MIB::ifInErrors" or
// "show interfaces counters errors" into a generic "unrecognised error".
export function inspectionReferenceText(reference) {
  if (reference === null || reference === undefined || reference === "") return "-";
  const text = String(reference);
  return REFERENCE_TEXT[text] ?? text;
}

export function inspectionItemLabel(id) {
  return ITEM_LABELS[id] ?? id;
}
