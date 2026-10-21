import re
from dataclasses import dataclass, field
from typing import Mapping

from app.services.resource_readings import cpu_readings, memory_readings, resource_percent
from app.services.interface_readings import (
    INTERFACE_NAME, interface_error_count, interface_error_details, interface_discard_count,
    interface_summary,
)


HOSTNAME_PATTERN = re.compile(r"(?im)^\s*(?:sysname|hostname)\s+(?P<hostname>[^\s#]+)\s*$")
STP_SUMMARY_ROW = re.compile(r"(?im)^\s*VLAN\d+\s+(?P<blocking>\d+)\s+\d+\s+\d+\s+\d+\s+\d+\s*$")

UPTIME_IS_PATTERN = re.compile(r"(?im)\buptime is\s+(?P<uptime>[^\r\n]+)")
UPTIME_COLON_PATTERN = re.compile(r"(?im)^\s*System uptime\s*:\s*(?P<uptime>[^<\r\n]+)")
CISCO_MODEL_PATTERN = re.compile(
    r"(?im)^\s*cisco\s+(?P<model>[A-Za-z0-9][A-Za-z0-9_.-]*)\s+\([^\r\n]*?\bprocessor\b"
)
CISCO_SOFTWARE_VERSION_PATTERN = re.compile(
    r"(?im)^\s*Cisco\s+(?:IOS(?:\s+XE)?|IOS-XE)\s+Software,\s+Version\s+(?P<version>[^,\r\n\s]+)"
)

def _search_text(patterns, text, field):
    for pattern in patterns:
        match = pattern.search(text)
        if match:
            return match.group(field).strip()
    return None


def _count_discarding(text):
    # A recognised port row is evidence even when every port is forwarding.
    # Empty tables and explanatory prose are not zero-count observations.
    rows = re.findall(
        r"(?im)^\s*(?:\d+\s+)?[A-Za-z][\w-]*\d[\w/.:_-]*\s+"
        r"(?:ROOT|DESG|DESI(?:GNATED)?|ALTN|ALTE(?:RNATE)?|BACK(?:UP)?|DISA(?:BLED)?)\s+"
        r"(FORWARDING|DISCARDING|BLOCKING|LEARNING|LISTENING|DISABLED|FWD|BLK|LRN)\b",
        text,
    )
    return sum(state.upper() in {"DISCARDING", "BLOCKING", "BLK"} for state in rows) if rows else None


def _count_cisco_blocking(text):
    rows = list(STP_SUMMARY_ROW.finditer(text))
    if rows:
        return sum(int(row.group("blocking")) for row in rows)
    return _count_discarding(text)


# --- 硬件与服务检查项解析 ---
# Comware reports a healthy card by its role (Master/Standby/Slave) rather
# than by a "Normal" state, so roles count as a working component.
NORMAL_WORDS = ("normal", "ok", "running", "present", "active", "registered", "good", "master", "standby", "slave")
ABNORMAL_WORDS = ("abnormal", "fault", "fail", "failed", "error", "alarm", "major", "minor", "fatal", "down", "offline", "absent", "missing", "not present")

# On a real chassis the "Slot/Card/..." line is the column header, not a card,
# and it contains the word "Alarm", so matching it reported a healthy chassis
# as having a faulty board. Data rows start with the slot identifier itself:
# a slot number for cards, PWRn for supplies and FANn for fan trays.
BOARD_ROW = re.compile(
    r"(?im)^\s*(?:\d+|(?:slot|card|board)\s*\d+|NAME:)\s+\S[^\n]*$"
)
POWER_ROW = re.compile(r"(?im)^\s*(?:power|pwr|psu)[-_ ]?\d*\s+\S[^\n]*$")
FAN_ROW = re.compile(r"(?im)^\s*(?:fan|fan-tray)[-_ ]?\d*\s+\S[^\n]*$")
# "Temp" only ever appears in the column header and in the "No temp
# sensors." notice, so the readings are identified by their status field
# instead. A trailing numeric column keeps prose lines out.
TEMP_ROW = re.compile(
    r"(?im)^\s*\S.*?\b(?P<status>NORMAL|MINOR|MAJOR|FATAL|ABNORMAL|ALARM)\b[^\n]*\d[^\n]*$"
)
COMMAND_HEADER = re.compile(r"(?m)^\$\s+(?P<command>[^\r\n]+)\r?\n?")
COMMAND_UNSUPPORTED = re.compile(
    r"(?im)(?:wrong parameter found|unrecognized command|unknown command|"
    r"invalid input detected|incomplete command|too many parameters|"
    r"^\s*(?:error|%)\s*:?[^\n]*\b(?:command|function)\b[^\n]*\bnot\s+supported\b)"
)
# Routers and some firewalls run with spanning tree off. That is a valid
# answer to the STP check, not an unreadable value.
STP_DISABLED = re.compile(
    r"(?im)(?:\b(?:STP|MSTP|RSTP|spanning[- ]tree)\b[^\n]*?\b(?:is\s+)?(?:not\s+(?:enabled|configured)|disabled)\b|"
    r"\bProtocol\s+Status\s*:\s*Disabled\b|\bSysStpStatus\s*:\s*DISABLED\b|"
    r"\bNo\s+spanning\s+tree\s+instances?\s+exists?\b)"
)
OUTPUT_TIMESTAMP = re.compile(
    r"^\s*\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:\s+[+-]\d{2}:\d{2})?\s*$"
)
TEMPERATURE_ALARM = re.compile(r"(?i)\b(?:temperature|temp|thermal|overheat(?:ing)?)\b")
HARDWARE_ALARMS = {
    "power_supplies": re.compile(
        r"(?i)(?:\bhwPower\w*|\bpower\b[^\r\n]*(?:absent|remove|fault|fail|offline|lost))"
    ),
    "fans": re.compile(
        r"(?i)(?:\bhwFan\w*|\bfan\b[^\r\n]*(?:absent|remove|fault|fail|offline|stopped))"
    ),
}


STORAGE_BYTES = re.compile(r"(?im)^[^\n]*?\b(?P<total>\d{6,})\b[^\n]*?\b(?P<free>\d{6,})\b")
STORAGE_PERCENT = re.compile(r"(?im)^[^\n]*\b(?:used|usage)\b[^:]*:?\s*(?P<pct>\d+)\s*%")
FILESYSTEM_ROW = re.compile(
    r"(?im)^\s*\*?\s*(?P<total>\d[\d,]*)\s+(?P<free>\d[\d,]*)\s+"
    r"(?P<type>\S+)\s+(?P<access>r[wo])\s+(?P<name>\S+:)\s*$"
)
DIRECTORY_HEADER = re.compile(r"(?im)^\s*Directory of\s+(?P<name>\S+?):?[/\\]?\s*$")
DIRECTORY_CAPACITY = re.compile(
    r"(?im)(?P<total>\d[\d,]*)\s*(?P<unit>bytes?|KB|MB|GB)\s+total\s*"
    r"\(\s*(?P<free>\d[\d,]*)\s*(?P=unit)\s+free\s*\)"
)
NTP_SYNC = re.compile(
    r"(?im)^[^\n]*\b(?:clock status\b[^:]*:?\s*|clock\s+is\s+)(?P<state>[a-zA-Z-]+)"
)
HUAWEI_HEALTH_TEMPERATURE_ROW = re.compile(
    r"(?im)^\s*(?:(?P<slot>\d+)\s+)?(?P<card>-|\d+)\s+(?P<sensor>\d+)\s+"
    r"(?P<status>normal|minor|major|fatal|abnormal|alarm)\s+"
    r"(?P<current>-?\d+(?:\.\d+)?)\s+(?P<lower>-?\d+(?:\.\d+)?)\s+"
    r"(?P<upper>-?\d+(?:\.\d+)?)\s*$"
)
HUAWEI_HEALTH_STORAGE_ROW = re.compile(
    r"(?im)^\s*(?P<slot>\d+)\s+(?P<name>\S+:)\s+(?P<total>\d+)\s+"
    r"(?P<used>\d+)\s+(?P<pct>\d+)\s*%\s*$"
)
NTP_UNSYNC_WORDS = ("unsynchronized", "unsync", "not synchronized", "unconfigured")
NTP_NOT_CONFIGURED = re.compile(
    r"(?im)\b(?:NTP\s+is\s+(?:not\s+enabled|disabled|not\s+configured)|"
    r"NTP\s+service\s+is\s+(?:disabled|not\s+configured)|no\s+NTP\s+(?:configuration|server))\b"
)
AGG_GROUP = re.compile(r'(?im)^\s*(?:Eth-Trunk|Po|AggregatePort|BAGG|Bridge-Aggregation)\s*?\d+[^\n]*$')
OSPF_NEIGHBOR = re.compile(r"(?im)^\s*(?P<id>\d+\.\d+\.\d+\.\d+)\s+[^\n]*$")
ESTABLISHED_WORDS = ("full", "established", "2-way")
CONFIG_SAVED_HINT = re.compile(
    r"(?im)(?:current|running)\s+configuration\s+is\s+(?:the\s+)?same\s+as\s+"
    r"(?:the\s+)?(?:(?:next\s+)?startup|saved)\s+configuration(?:\s+file)?|"
    r"\bconfiguration\s+(?:has\s+not\s+been\s+modified|is\s+unchanged|is\s+saved)\b|"
    r"当前配置与已保存配置一致"
)
CONFIG_UNSAVED_HINT = re.compile(
    r"(?im)\b(?:the\s+current\s+configuration\s+is\s+not\s+the\s+same\s+as\s+"
    r"the\s+next\s+startup\s+configuration\s+file|"
    r"unsaved\s+(?:configuration\s+)?changes?|configuration\s+(?:is\s+not\s+saved|"
    r"has\s+been\s+modified)|current\s+configuration\s+(?:differs\s+from|is\s+different\s+from)\s+"
    r"(?:the\s+)?(?:saved|startup)\s+configuration)\b|"
    r"^\s*(?:startup|next startup)\s+saved-configuration\s+file\s*:\s*(?:NULL|None)\s*$|"
    r"当前配置存在未保存"
)
CONFIG_STARTUP_FILE_HINT = re.compile(
    r"(?im)^\s*(?:startup|next startup)\s+saved-configuration\s+file\s*:|"
    r"^\s*BOOT\s+variable\s*="
)



def _component_status(row: str) -> str:
    low = row.lower()
    if any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low) for word in ABNORMAL_WORDS):
        return "abnormal"
    if any(re.search(rf"(?<![a-z]){word}(?![a-z])", low) for word in ("unknown", "unregistered")):
        return "unknown"
    if any(re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low) for word in NORMAL_WORDS):
        return "normal"
    return "unknown"


def _component_from_row(row: str) -> dict[str, str]:
    fields = row.split()
    component_id = fields[0] if fields else "-"
    model_index = 1
    if component_id.lower() in {"slot", "card", "board", "power", "fan", "psu"} and len(fields) > 1:
        component_id = f"{component_id} {fields[1]}"
        model_index = 2
    elif len(fields) > 1 and fields[1] == "-":
        model_index = 2
    model = fields[model_index] if len(fields) > model_index else "-"
    return {
        "id": component_id,
        "model": model,
        "status": _component_status(row),
        "summary": row.strip(),
    }


def _component_summary(rows: list[str]) -> dict[str, object] | None:
    if not rows:
        return None
    components = [_component_from_row(row) for row in rows]
    return {
        "total": len(components),
        "abnormal": sum(component["status"] == "abnormal" for component in components),
        "components": components,
    }


def _status_counts(pattern, text):
    rows = [r for r in pattern.findall(text) if r.strip()]
    return _component_summary(rows)


def _h3c_power(text):
    components = []
    in_table = False
    for line in text.splitlines():
        if re.match(r"(?i)^\s*PowerID\s+State\s+", line):
            in_table = True
            continue
        if line.strip().startswith("$"):
            in_table = False
        if not in_table:
            continue
        match = re.match(r"(?i)^\s*(\d+)\s+(Normal|Absent|Fault|Unknown)\b", line)
        if match:
            components.append({
                "id": match.group(1), "model": "-", "status": _component_status(match.group(2)),
                "summary": line.strip(),
            })
    if not components:
        return _status_counts(POWER_ROW, text)
    return {"total": len(components), "abnormal": sum(row["status"] == "abnormal" for row in components), "components": components}


def _h3c_fans(text):
    components = []
    current = None
    slot = None
    for line in text.splitlines():
        if line.strip().startswith("$"):
            current = None
            slot = None
            continue
        slot_match = re.match(r"(?i)^\s*Slot\s+(\d+)\s*:\s*$", line)
        if slot_match:
            slot = slot_match.group(1)
            current = None
            continue
        # Comware 5 prints "Fan Frame 1"; Comware 7 prints "Fan 1:" under
        # each IRF member's "Slot N:" heading.
        match = re.match(r"(?i)^\s*Fan\s+(?:Frame\s+)?(\d+)\s*:?(?:\s|$)", line)
        if match:
            fan_id = f"{slot}/{match.group(1)}" if slot else match.group(1)
            current = {"id": fan_id, "model": "-", "status": "unknown", "summary": line.strip()}
            components.append(current)
        state = re.search(r"(?i)\bState\s*:\s*(\S+)", line)
        if current is not None and state:
            value = state.group(1)
            current["status"] = "abnormal" if value.lower() == "fandirectionfault" else _component_status(value)
            if not match:
                current["summary"] += f"; {line.strip()}"
    if not components:
        return _status_counts(FAN_ROW, text)
    return {"total": len(components), "abnormal": sum(row["status"] == "abnormal" for row in components), "components": components}


def _huawei_hardware(text):
    rows = {"boards": [], "power_supplies": [], "fans": []}
    
    # 检测是否为防火墙表格格式 (包含表头行)
    is_table_format = re.search(r"Slot\s+Sub\s+Type", text, re.IGNORECASE) is not None
    
    if is_table_format:
        # 防火墙表格格式解析
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or set(stripped) <= {"-"}:
                continue
            # 跳过表头和分隔线
            if re.match(r"^(Slot|Sub|Type|Online|Power|Register|Status|Role)", stripped, re.IGNORECASE):
                continue
            if re.match(r"^[-=]+$", stripped):
                continue
            
            fields = stripped.split()
            if len(fields) < 3:
                continue

            # 交换机的电源/风扇行缩进在所属槽位下，没有 Slot 列：
            # "PWR1 POWER Present PowerOn Registered Normal NA"
            if re.fullmatch(r"(?i)(?:pwr|power|psu)\d*", fields[0]) or fields[1].upper() in {"POWER", "PWR", "PSU"}:
                rows["power_supplies"].append(stripped)
                continue
            if re.fullmatch(r"(?i)fan\d*", fields[0]) or fields[1].upper() == "FAN":
                rows["fans"].append(stripped)
                continue

            # 第一列是 Slot 编号，第三列是 Type
            if not fields[0].isdigit():
                continue
            device_type = fields[2].upper()

            # 根据 Type 分类
            if device_type in {"PWR", "POWER", "PSU"}:
                rows["power_supplies"].append(stripped)
            elif device_type == "FAN":
                rows["fans"].append(stripped)
            else:
                # RPU, FIBA 等单板类型
                rows["boards"].append(stripped)
    else:
        # 交换机/路由器行格式解析 (原有逻辑)
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or set(stripped) <= {"-"}:
                continue
            low = stripped.lower()
            if re.match(r"^(?:power|pwr|psu)[-_ ]?\d*(?:\s|$)", low):
                rows["power_supplies"].append(stripped)
                continue
            if re.match(r"^(?:fan|fan-tray)[-_ ]?\d*(?:\s|$)", low):
                rows["fans"].append(stripped)
                continue
            if re.match(r"^(?:slot|card|board)\s+\d+\s", low):
                rows["boards"].append(stripped)
                continue
            if not re.match(r"^\d+\s+", stripped):
                continue
            fields = stripped.split()
            subtype = fields[2].lower() if len(fields) > 2 and fields[1] == "-" else ""
            if subtype in {"pwr", "power", "psu"}:
                rows["power_supplies"].append(stripped)
            elif subtype.startswith("fan"):
                rows["fans"].append(stripped)
            else:
                rows["boards"].append(stripped)

    return {
        item: summary
        for item, item_rows in rows.items()
        if (summary := _component_summary(item_rows)) is not None
    }


def _temperature(text):
    numeric_readings = []
    in_numeric_table = False
    for line in text.splitlines():
        if re.fullmatch(r"(?i)\s*Board\s+Temperature\s+Lower\s+limit\s+Upper\s+limit\s*", line):
            in_numeric_table = True
            continue
        if not in_numeric_table:
            continue
        if line.strip().startswith("$"):
            in_numeric_table = False
            continue
        match = re.fullmatch(
            r"\s*(\d+(?:/\d+)*)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*",
            line,
        )
        if not match:
            if line.strip() and not re.fullmatch(r"[-=+\s]+", line) and not re.match(r"\s*\d", line):
                in_numeric_table = False
            continue
        current, lower, upper = [float(value) for value in match.groups()[1:]]
        if lower >= upper:
            continue
        current, lower, upper = [int(value) if value.is_integer() else value for value in (current, lower, upper)]
        numeric_readings.append({
            "name": match.group(1), "current": current, "unit": "C",
            "status": "normal" if lower <= current <= upper else "alarm",
            "thresholds": {"lower": lower, "upper": upper},
        })
    if numeric_readings:
        return {
            "sensors": len(numeric_readings),
            "alarming": sum(reading["status"] != "normal" for reading in numeric_readings),
            "max_current": max(reading["current"] for reading in numeric_readings),
            "readings": numeric_readings,
        }

    health_readings = []
    current_slot = None
    for match in HUAWEI_HEALTH_TEMPERATURE_ROW.finditer(text):
        if match.group("slot") is not None:
            current_slot = match.group("slot")
        if current_slot is None:
            continue
        current = float(match.group("current"))
        lower = float(match.group("lower"))
        upper = float(match.group("upper"))
        health_readings.append({
            "name": f"{current_slot}/{match.group('card')}/{match.group('sensor')}",
            "current": int(current) if current.is_integer() else current,
            "unit": "C",
            "status": match.group("status").lower(),
            "thresholds": {
                "lower": int(lower) if lower.is_integer() else lower,
                "upper": int(upper) if upper.is_integer() else upper,
            },
        })
    if health_readings:
        return {
            "sensors": len(health_readings),
            "alarming": sum(reading["status"] != "normal" for reading in health_readings),
            "max_current": max(reading["current"] for reading in health_readings),
            "readings": health_readings,
        }

    rows = [match.group(0) for match in TEMP_ROW.finditer(text) if match.group(0).strip()]
    readings = []
    for row in rows:
        fields = row.split()
        status_index = next(
            (index for index, field in enumerate(fields) if field.lower() in {
                "normal", "minor", "major", "fatal", "abnormal", "alarm",
            }),
            None,
        )
        if status_index is None or len(fields) <= status_index + 1:
            continue
        trailing_numbers = []
        for value in fields[status_index + 1:]:
            try:
                trailing_numbers.append(float(value))
            except ValueError:
                continue
        if not trailing_numbers:
            continue
        current = trailing_numbers[-1]
        thresholds = {}
        if len(trailing_numbers) >= 4:
            thresholds = {
                "minor": trailing_numbers[0],
                "major": trailing_numbers[1],
                "fatal": trailing_numbers[2],
            }
        name_parts = fields[:status_index]
        name = "/".join(name_parts[:4]) if name_parts else f"sensor-{len(readings) + 1}"
        status = fields[status_index].lower()
        reading = {
            "name": name,
            "current": int(current) if current.is_integer() else current,
            "unit": "C",
            "status": status,
            "thresholds": {
                key: int(value) if value.is_integer() else value for key, value in thresholds.items()
            },
        }
        readings.append(reading)
    if not readings and re.search(r"(?i)\bno\s+(?:temp(?:erature)?\s+)?sensors?\b", text):
        return {"sensors": 0, "alarming": 0, "readings": []}
    if not readings:
        return None
    return {
        "sensors": len(readings),
        "alarming": sum(reading["status"] != "normal" for reading in readings),
        "max_current": max(reading["current"] for reading in readings),
        "readings": readings,
    }


def _media_type(name: str, reported_type: str = "") -> str:
    normalized = f"{name} {reported_type}".lower()
    for media_type in ("bootflash", "cfcard", "flash", "hda"):
        if media_type in normalized:
            return "cf" if media_type == "cfcard" else media_type
    if re.search(r"\bcf\d*:?", normalized):
        return "cf"
    return reported_type.lower() or "storage"


def _capacity_bytes(value: str, unit: str) -> int:
    multiplier = {"byte": 1, "bytes": 1, "kb": 1024, "mb": 1024 ** 2, "gb": 1024 ** 3}
    return int(value.replace(",", "")) * multiplier[unit.lower()]


def _storage(text):
    health_filesystems = []
    for match in HUAWEI_HEALTH_STORAGE_ROW.finditer(text):
        total_mb = int(match.group("total"))
        used_mb = int(match.group("used"))
        used_percent = int(match.group("pct"))
        if total_mb <= 0 or used_mb > total_mb or not 0 <= used_percent <= 100:
            continue
        name = match.group("name")
        health_filesystems.append({
            "name": name,
            "media_type": _media_type(name),
            "total_bytes": total_mb * 1024 ** 2,
            "free_bytes": (total_mb - used_mb) * 1024 ** 2,
            "used_percent": used_percent,
        })
    if health_filesystems:
        return {
            "used_percent": max(filesystem["used_percent"] for filesystem in health_filesystems),
            "filesystems": health_filesystems,
        }

    filesystems = []
    for match in FILESYSTEM_ROW.finditer(text):
        total = int(match.group("total").replace(",", ""))
        free = int(match.group("free").replace(",", ""))
        if total <= 0 or free > total:
            continue
        name = match.group("name")
        access = match.group("access").lower()
        filesystems.append({
            "name": name,
            "media_type": _media_type(name, match.group("type")),
            "total_bytes": total,
            "free_bytes": free,
            "used_percent": round((total - free) / total * 100),
            "access": access,
            "status": "normal" if access == "rw" else "read_only",
        })

    directory_headers = list(DIRECTORY_HEADER.finditer(text))
    for index, header in enumerate(directory_headers):
        end = directory_headers[index + 1].start() if index + 1 < len(directory_headers) else len(text)
        capacity = DIRECTORY_CAPACITY.search(text, header.end(), end)
        if not capacity:
            continue
        total = _capacity_bytes(capacity.group("total"), capacity.group("unit"))
        free = _capacity_bytes(capacity.group("free"), capacity.group("unit"))
        if total <= 0 or free > total:
            continue
        name = header.group("name").rstrip("/\\")
        if not name.endswith(":"):
            name += ":"
        filesystems.append({
            "name": name,
            "media_type": _media_type(name),
            "total_bytes": total,
            "free_bytes": free,
            "used_percent": round((total - free) / total * 100),
            "access": "unknown",
            "status": "unknown",
        })

    if filesystems:
        return {
            "used_percent": max(filesystem["used_percent"] for filesystem in filesystems),
            "filesystems": filesystems,
        }
    # Generic usage/byte columns need storage evidence; CPU/memory values must
    # not be interpreted as disk space by the direct, unscoped parser.
    storage_text = "\n".join(line for line in text.splitlines() if re.search(
        r"(?i)\b(?:storage|disk|filesystem|flash|bootflash|cfcard|hda\d*)\b", line,
    ))
    m = STORAGE_PERCENT.search(storage_text)
    if m:
        percent = int(m.group("pct"))
        return {"used_percent": percent} if 0 <= percent <= 100 else None
    m = STORAGE_BYTES.search(storage_text)
    if not m:
        return None
    total = int(m.group("total"))
    free = int(m.group("free"))
    if not total or free > total:
        return None
    return {"used_percent": round((total - free) / total * 100)}


def _merge_storage_values(values: list[dict]) -> dict | None:
    """Merge storage rows returned by multiple commands without losing media."""
    filesystems: list[dict] = []
    seen_names: set[str] = set()
    scalar_values: list[float] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        used = value.get("used_percent")
        if isinstance(used, (int, float)):
            scalar_values.append(used)
        for filesystem in value.get("filesystems") or []:
            if not isinstance(filesystem, dict):
                continue
            name = str(filesystem.get("name", "")).strip().lower()
            if not name or name in seen_names:
                continue
            seen_names.add(name)
            filesystems.append(filesystem)
    if not filesystems and not scalar_values:
        return None
    merged = {"used_percent": max(scalar_values)} if scalar_values else {}
    if filesystems:
        merged["filesystems"] = filesystems
        merged["used_percent"] = max(
            filesystem["used_percent"] for filesystem in filesystems
            if isinstance(filesystem.get("used_percent"), (int, float))
        )
    return merged


def _active_alarms(text):
    if not re.search(
        r"(?im)^\$\s+(?:display alarm active|show alarm|show facility-alarm status)\s*$",
        text,
    ):
        return None
    alarms = []
    structured_rows = []
    explicitly_empty = bool(re.search(
        r"(?im)\b(?:no|none|zero)\s+(?:active\s+)?alarms?\b|\b0\s+active\s+alarms?\b",
        text,
    ))
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("$"):
            continue
        fields = stripped.split("/")
        if len(fields) >= 9 and fields[0].isdigit():
            severity = fields[6].strip().lower()
            structured_rows.append({
                "id": fields[0].strip(),
                "name": fields[5].strip(),
                "severity": severity,
                "status": fields[7].strip().lower(),
                "description": "/".join(fields[8:]).strip(),
            })
    confirmed_empty = explicitly_empty or "A/B/C/D/E/F/G/H/I/J" in text
    if structured_rows:
        alarms = structured_rows
    elif confirmed_empty:
        alarms = []
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if (
                not stripped
                or stripped.startswith("$")
                or set(stripped) <= {"-", "=", "+", "|"}
                or re.fullmatch(
                    r"(?i)(?:active\s+)?alarms?(?:\s+(?:information|status|list|table|details?))?:?",
                    stripped,
                )
            ):
                continue
            low = stripped.lower()
            severity = next(
                (
                    marker
                    for marker in ("critical", "fatal", "emergency", "major", "error", "warning", "minor")
                    if re.search(rf"(?<![a-z]){marker}(?![a-z])", low)
                ),
                None,
            )
            active_alarm = "alarm" in low and bool(re.search(r"(?i)\b(?:active|raised|start(?:ed)?)\b", low))
            failed_component = bool(re.search(r"(?i)\b(?:fault|failed?|offline|absent|removed?)\b", low))
            if not (severity or active_alarm or failed_component):
                continue
            alarms.append({
                "id": str(len(alarms) + 1),
                "name": "device-alarm",
                "severity": severity or "warning",
                "status": "active",
                "description": stripped,
            })
    if not alarms and not confirmed_empty:
        return None
    critical_levels = {"critical", "fatal", "emergency", "alert"}
    warning_levels = {"major", "minor", "warning", "error", "alarm"}
    return {
        "total": len(alarms),
        "critical": sum(alarm["severity"] in critical_levels for alarm in alarms),
        "warning": sum(alarm["severity"] in warning_levels for alarm in alarms),
        "alarms": alarms,
    }


def _ntp(text):
    if NTP_NOT_CONFIGURED.search(text):
        return {"configured": False, "synchronized": None}
    m = NTP_SYNC.search(text)
    if not m:
        return None
    state = m.group("state").lower()
    synced = state.startswith("sync") and not any(w in state for w in NTP_UNSYNC_WORDS)
    return {"synchronized": bool(synced)}


def _aggregation_summary(groups: list[dict]) -> dict:
    return {
        "groups": len(groups),
        "degraded": sum(bool(group["degraded"]) for group in groups),
        "details": groups,
    }


def _huawei_eth_trunks(text):
    # VRP prints one block per trunk; state lives on the lines that follow
    # "Eth-TrunkN's state information is:", never on the heading itself.
    groups = []
    current = None
    for line in text.splitlines():
        heading = re.match(r"(?i)^\s*(Eth-Trunk\s*\d+)'s\s+state\s+information\s+is", line)
        if heading:
            current = {"name": heading.group(1).replace(" ", ""), "status": None, "down_members": [], "degraded": False}
            groups.append(current)
            continue
        if current is None:
            continue
        status = re.search(r"(?i)\bOperate\s+status\s*:\s*(\w+)", line)
        if status:
            current["status"] = status.group(1).lower()
        member = re.match(r"(?i)^\s*(\S*\d\S*)\s+(Up|Down|Selected|Unselect(?:ed)?)\b", line)
        if member and member.group(2).lower() in {"down", "unselect", "unselected"}:
            current["down_members"].append(member.group(1))
    for group in groups:
        group["degraded"] = group["status"] not in {None, "up"} or bool(group["down_members"])
    return _aggregation_summary(groups) if groups else None


def _h3c_link_aggregations(text):
    groups = []
    current = None
    remote = False
    for line in text.splitlines():
        # Comware 7: "Aggregate Interface: Bridge-Aggregation1".
        # Comware 5: "Aggregation ID: 1,  AggregationType: Static, ...".
        heading = re.match(r"(?i)^\s*Aggregat(?:e|ion)\s+(Interface|ID)\s*:\s*([^,\s]+)", line)
        if heading:
            name = heading.group(2) if heading.group(1).lower() == "interface" else f"Aggregation {heading.group(2)}"
            current = {"name": name, "status": None, "down_members": [], "degraded": False}
            groups.append(current)
            remote = False
            continue
        if current is None:
            continue
        if re.match(r"(?i)^\s*(?:Remote|Peer)\s*:", line):
            remote = True
        elif re.match(r"(?i)^\s*Local\s*:", line):
            remote = False
        # Local port rows: "GE1/0/1  S  32768  1  {ACDEF}"; U means unselected.
        member = re.match(r"^\s*(\S*\d\S*)\s+([SUI])\s+\d", line)
        if member and not remote and member.group(2) == "U":
            current["down_members"].append(member.group(1))
    for group in groups:
        group["degraded"] = bool(group["down_members"])
    return _aggregation_summary(groups) if groups else None


def _cisco_etherchannels(text):
    if re.search(r"(?i)Number\s+of\s+channel-groups\s+in\s+use\s*:\s*0\b", text):
        return _aggregation_summary([])
    if not re.search(r"(?i)\bGroup\s+Port-channel\b", text):
        return None
    groups = []
    for line in text.splitlines():
        row = re.match(r"^\s*\d+\s+(Po\d+)\((\w+)\)\s+\S+\s*(.*)$", line)
        if row:
            groups.append({"name": row.group(1), "status": row.group(2), "down_members": [], "degraded": False})
            members = row.group(3)
        elif groups and re.match(r"^\s+\S+\(\w+\)", line):
            # Long member lists continue on indented lines.
            members = line
        else:
            continue
        for member, flags in re.findall(r"(\S+)\((\w+)\)", members):
            if "P" not in flags and "H" not in flags:
                groups[-1]["down_members"].append(member)
    for group in groups:
        # "SU" is a layer-2 channel in use, "RU" layer-3; D means down.
        group["degraded"] = "D" in group["status"] or "U" not in group["status"] or bool(group["down_members"])
    return _aggregation_summary(groups)


def _ruijie_aggregate_ports(text):
    if not re.search(r"(?i)^\s*AggregatePort\s+MaxPorts\b", text, re.MULTILINE):
        return None
    groups = [
        {"name": match.group(1), "status": None, "down_members": [], "degraded": False}
        for match in re.finditer(r"(?im)^\s*(Ag\d+)\s+\d+\s", text)
    ]
    return _aggregation_summary(groups)


def _link_aggregation(text):
    for parser in (_huawei_eth_trunks, _h3c_link_aggregations, _cisco_etherchannels, _ruijie_aggregate_ports):
        value = parser(text)
        if value is not None:
            return value
    rows = [r for r in AGG_GROUP.findall(text) if r.strip()]
    if not rows:
        return None
    degraded = sum(1 for r in rows if any(w in r.lower() for w in ABNORMAL_WORDS))
    return {"groups": len(rows), "degraded": degraded}


def _routing_neighbors(text):
    rows = [r for r in OSPF_NEIGHBOR.findall(text)]
    if not rows:
        return None
    lines = [line for line in text.splitlines() if OSPF_NEIGHBOR.match(line)]
    established = sum(
        1 for line in lines if any(word in line.lower() for word in ESTABLISHED_WORDS)
    )
    return {"total": len(lines), "established": established}


def _configuration_saved(text):
    if CONFIG_UNSAVED_HINT.search(text):
        return {"saved": False}
    if CONFIG_SAVED_HINT.search(text):
        return {"saved": True}
    return None


def _interface_errors_text(text):
    return interface_error_count(text)


def _counter_heading(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", value.lower())


def _cisco_interface_counter_table(text: str) -> dict | None:
    receive_totals = ("rcverr", "inerrors", "inputerrors")
    transmit_totals = ("xmiterr", "outerrors", "outputerrors")
    receive_components = (
        "alignerr", "fcserr", "crc", "crcerrors", "undersize", "oversize", "runts", "giants",
    )
    transmit_components = ("latecoll", "excesscol", "carrisen", "collisions")
    input_discards = ("indiscards", "inputdiscards")
    output_discards = ("outdiscards", "outputdiscards")
    recognized = set(
        receive_totals + transmit_totals + receive_components + transmit_components
        + input_discards + output_discards
    )

    port_counters: dict[str, dict[str, int]] = {}
    headings: list[str] | None = None
    for line in text.splitlines():
        fields = line.split()
        normalized = [_counter_heading(field) for field in fields]
        if fields and normalized[0] in {"port", "interface"}:
            # Every table header ends the previous table. This prevents a
            # following Cisco counter table from being decoded with stale
            # column positions even when none of its fields are supported.
            headings = normalized if recognized.intersection(normalized[1:]) else None
            continue
        if not headings or len(fields) < len(headings) or not INTERFACE_NAME.fullmatch(fields[0]):
            continue
        counters = port_counters.setdefault(fields[0], {})
        for index, heading in enumerate(headings[1:], start=1):
            try:
                value = int(fields[index].replace(",", ""))
            except (IndexError, ValueError):
                continue
            counters[heading] = max(counters.get(heading, 0), value)
    if not port_counters:
        return None

    def first_value(counters: dict[str, int], names: tuple[str, ...]) -> int | None:
        return next((counters[name] for name in names if name in counters), None)

    def component_total(counters: dict[str, int], names: tuple[str, ...]) -> int | None:
        values = [counters[name] for name in names if name in counters]
        return sum(values) if values else None

    details = []
    has_errors = False
    has_discards = False
    for interface_name, counters in port_counters.items():
        received = first_value(counters, receive_totals)
        if received is None:
            received = component_total(counters, receive_components)
        transmitted = first_value(counters, transmit_totals)
        if transmitted is None:
            transmitted = component_total(counters, transmit_components)
        discarded_in = component_total(counters, input_discards)
        discarded_out = component_total(counters, output_discards)
        if all(value is None for value in (received, transmitted, discarded_in, discarded_out)):
            continue

        detail = {"name": interface_name}
        if received is not None:
            detail["input_errors"] = received
            has_errors = True
        if transmitted is not None:
            detail["output_errors"] = transmitted
            has_errors = True
        if has_errors and (received is not None or transmitted is not None):
            detail["total_errors"] = (received or 0) + (transmitted or 0)
        if discarded_in is not None:
            detail["input_discards"] = discarded_in
            has_discards = True
        if discarded_out is not None:
            detail["output_discards"] = discarded_out
            has_discards = True
        details.append(detail)

    if not details:
        return None
    metrics = {"interface_error_details": details}
    if has_errors:
        metrics["interface_error_count"] = sum(detail.get("total_errors", 0) for detail in details)
    if has_discards:
        metrics["interface_discard_count"] = sum(
            detail.get("input_discards", 0) + detail.get("output_discards", 0)
            for detail in details
        )
    return metrics


def _cisco_interface_errors(text):
    direct = _interface_errors_text(text)
    if direct is not None:
        return direct
    table = _cisco_interface_counter_table(text)
    return table.get("interface_error_count") if table else None


def _cisco_interface_error_details(text):
    direct = interface_error_details(text)
    if direct is not None:
        return direct
    table = _cisco_interface_counter_table(text)
    return table.get("interface_error_details") if table else None


def _cisco_interface_discards(text):
    direct = interface_discard_count(text)
    if direct is not None:
        return direct
    table = _cisco_interface_counter_table(text)
    return table.get("interface_discard_count") if table else None


def _interface_summary(text):
    return interface_summary(text)


VENDOR_PARSERS = {
    "Huawei": {
        "uptime": lambda t: _search_text((UPTIME_IS_PATTERN,), t, "uptime"),
        "stp_discarding_ports": _count_discarding,
        "active_alarms": _active_alarms,
        "boards": lambda t: _huawei_hardware(t).get("boards"),
        "power_supplies": lambda t: _huawei_hardware(t).get("power_supplies"),
        "fans": lambda t: _huawei_hardware(t).get("fans"),
        "temperature": _temperature,
        "interface_error_count": _interface_errors_text,
        "link_aggregation": _link_aggregation,
        "routing_neighbors": _routing_neighbors,
        "ntp": _ntp,
        "storage": _storage,
        "configuration_saved": _configuration_saved,
    },
    "H3C": {
        "uptime": lambda t: _search_text((UPTIME_IS_PATTERN, UPTIME_COLON_PATTERN), t, "uptime"),
        "stp_discarding_ports": _count_discarding,
        "active_alarms": _active_alarms,
        "boards": lambda t: _status_counts(BOARD_ROW, t),
        "power_supplies": lambda t: _status_counts(POWER_ROW, t),
        "fans": lambda t: _status_counts(FAN_ROW, t),
        "temperature": _temperature,
        "interface_error_count": _interface_errors_text,
        "link_aggregation": _link_aggregation,
        "routing_neighbors": _routing_neighbors,
        "ntp": _ntp,
        "storage": _storage,
        "configuration_saved": _configuration_saved,
    },
    "Ruijie": {
        "uptime": lambda t: _search_text((UPTIME_COLON_PATTERN, UPTIME_IS_PATTERN), t, "uptime"),
        "stp_discarding_ports": _count_discarding,
        "active_alarms": _active_alarms,
        "boards": lambda t: _status_counts(BOARD_ROW, t),
        "power_supplies": lambda t: _status_counts(POWER_ROW, t),
        "fans": lambda t: _status_counts(FAN_ROW, t),
        "temperature": _temperature,
        "interface_error_count": _cisco_interface_errors,
        "link_aggregation": _link_aggregation,
        "routing_neighbors": _routing_neighbors,
        "ntp": _ntp,
        "storage": _storage,
        "configuration_saved": _configuration_saved,
    },
    "Cisco": {
        "uptime": lambda t: _search_text((UPTIME_IS_PATTERN,), t, "uptime"),
        "stp_discarding_ports": _count_cisco_blocking,
        "active_alarms": _active_alarms,
        "boards": lambda t: _status_counts(BOARD_ROW, t),
        "power_supplies": lambda t: _status_counts(POWER_ROW, t),
        "fans": lambda t: _status_counts(FAN_ROW, t),
        "temperature": _temperature,
        "interface_error_count": _cisco_interface_errors,
        "link_aggregation": _link_aggregation,
        "routing_neighbors": _routing_neighbors,
        "ntp": _ntp,
        "storage": _storage,
        "configuration_saved": _configuration_saved,
    },
}

# Keep scalar metrics and their object details on the same bounded parser.
for _vendor, _extractors in VENDOR_PARSERS.items():
    _extractors["cpu"] = lambda text, vendor=_vendor: resource_percent(cpu_readings(vendor, text))
    _extractors["cpu_readings"] = lambda text, vendor=_vendor: cpu_readings(vendor, text)
    _extractors["memory"] = lambda text, vendor=_vendor: resource_percent(memory_readings(vendor, text))
    _extractors["memory_readings"] = lambda text, vendor=_vendor: memory_readings(vendor, text)
    _extractors["interface_error_details"] = interface_error_details
    _extractors["interface_discard_count"] = interface_discard_count

for _vendor in ("Cisco", "Ruijie"):
    VENDOR_PARSERS[_vendor]["interface_error_details"] = _cisco_interface_error_details
    VENDOR_PARSERS[_vendor]["interface_discard_count"] = _cisco_interface_discards

VENDOR_PARSERS["H3C"]["power_supplies"] = _h3c_power
VENDOR_PARSERS["H3C"]["fans"] = _h3c_fans


METRIC_ITEMS = {
    "hostname": "device_information",
    "model": "device_information",
    "software_version": "device_information",
    "uptime": "uptime",
    "cpu": "cpu",
    "cpu_readings": "cpu",
    "memory": "memory",
    "memory_readings": "memory",
    "stp_discarding_ports": "stp",
    "active_alarms": "active_alarms",
    "boards": "boards",
    "power_supplies": "power_supplies",
    "fans": "fans",
    "temperature": "temperature",
    "interface_error_count": "interface_errors",
    "interface_error_details": "interface_errors",
    "interface_discard_count": "interface_errors",
    "link_aggregation": "link_aggregation",
    "routing_neighbors": "routing_neighbors",
    "ntp": "ntp",
    "storage": "storage",
    "configuration_saved": "configuration_saved",
}


@dataclass
class SshParseResult:
    metrics: dict[str, object] = field(default_factory=dict)
    unsupported_checks: set[str] = field(default_factory=set)
    unsupported_reasons: dict[str, str] = field(default_factory=dict)
    item_messages: dict[str, str] = field(default_factory=dict)
    item_references: dict[str, str] = field(default_factory=dict)


def _command_sections(raw_output: str) -> dict[str, str]:
    matches = list(COMMAND_HEADER.finditer(raw_output))
    sections: dict[str, str] = {}
    for index, match in enumerate(matches):
        command = match.group("command").strip().lower()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(raw_output)
        output = raw_output[match.end():end].strip("\r\n")
        if command in sections:
            sections[command] = f"{sections[command]}\n{output}"
        else:
            sections[command] = output
    return sections


def _scoped_output(commands: list[str], sections: Mapping[str, str]) -> str:
    return "\n\n".join(f"$ {command}\n{sections[command]}" for command in commands)


def _effectively_empty(output: str) -> bool:
    return not any(
        line.strip() and not OUTPUT_TIMESTAMP.match(line)
        for line in output.splitlines()
    )


def _active_temperature_alarms(output: str) -> dict[str, int]:
    alarm_rows = [
        line for line in output.splitlines()
        if "/" in line and TEMPERATURE_ALARM.search(line)
    ]
    return {"sensors": len(alarm_rows), "alarming": len(alarm_rows)}


def _active_hardware_alarm_count(output: str, pattern: re.Pattern) -> int:
    return sum(
        1 for line in output.splitlines()
        if re.match(r"^\s*\d+/", line) and pattern.search(line)
    )


def _hardware_alarm_details(output: str, pattern: re.Pattern) -> list[dict[str, str]]:
    details = []
    for line in output.splitlines():
        fields = line.strip().split("/")
        if len(fields) < 9 or not fields[0].isdigit() or not pattern.search(line):
            continue
        details.append({
            "id": fields[0].strip(),
            "name": fields[5].strip(),
            "severity": fields[6].strip().lower(),
            "description": "/".join(fields[8:]).strip(),
        })
    return details


PHYSICAL_NAME = re.compile(r"(?i)\bPhysicalName\s*=\s*[\"']?([^\"'\s,)]+)")


def _alarm_component(details: dict[str, str], components: list[dict]) -> dict | None:
    description = str(details.get("description") or "")
    match = PHYSICAL_NAME.search(description)
    if not match:
        return components[0] if len(components) == 1 else None
    physical_name = match.group(1).strip().lower()
    for component in components:
        component_id = str(component.get("id") or "").strip().lower()
        model = str(component.get("model") or "").strip().lower()
        if physical_name in {component_id, model}:
            return component
        suffix = re.search(r"(\d+)$", physical_name)
        if suffix and component_id == suffix.group(1) and model and physical_name.startswith(model):
            return component
    return None


def parse_device_identity(vendor: str, text: str) -> dict[str, str]:
    identity = {}
    hostname = HOSTNAME_PATTERN.search(text)
    if hostname:
        identity["hostname"] = hostname.group("hostname")
    if vendor == "Cisco":
        model = CISCO_MODEL_PATTERN.search(text)
        version = CISCO_SOFTWARE_VERSION_PATTERN.search(text)
    elif vendor in {"Huawei", "H3C"}:
        model = re.search(
            rf"(?im)^\s*{vendor}\s+(?P<model>[A-Za-z0-9][\w.-]*)\s+uptime\s+is\b", text,
        )
        if vendor == "Huawei":
            version = re.search(
                r"(?im)^\s*VRP[^\r\n]*\bVersion\b[^\r\n]*\b(?P<version>V\d+R\d+[A-Za-z0-9]*)\b", text,
            ) or re.search(r"(?im)^\s*VRP[^\r\n]*?\bVersion\s+(?P<version>[\d.]+)\b", text)
        else:
            version = re.search(
                r"(?im)^\s*(?:H3C\s+)?Comware\s+Software,\s*Version\s+"
                r"(?P<version>[\w.]+(?:,\s*Release\s+[\w.]+)?)", text,
            )
    elif vendor == "Ruijie":
        model = re.search(
            r"(?im)^\s*System description\s*:[^\r\n]*\((?P<model>[A-Za-z][\w.-]*\d[\w.-]*)\)\s*$", text,
        )
        version = re.search(r"(?im)^\s*System software version\s*:\s*(?P<version>[^\r\n]+)", text)
    else:
        model = version = None
    if model:
        identity["model"] = model.group("model").strip()
    if version:
        identity["software_version"] = version.group("version").strip()
    return identity


def parse_ssh_output(
    vendor: str,
    raw_output: str,
    command_items: Mapping[str, set[str]],
) -> SshParseResult:
    """Parse each metric only from the command that owns that inspection item."""
    extractors = VENDOR_PARSERS[vendor]
    sections = _command_sections(raw_output)
    if not sections:
        return SshParseResult(metrics=parse_ssh_metrics(vendor, raw_output))

    normalized_items = {command.lower(): items for command, items in command_items.items()}
    unsupported_commands = {
        command for command, output in sections.items() if COMMAND_UNSUPPORTED.search(output)
    }
    result = SshParseResult()

    for item_id in set().union(*normalized_items.values()):
        item_commands = [
            command for command, items in normalized_items.items()
            if item_id in items and command in sections
        ]
        if item_commands and all(command in unsupported_commands for command in item_commands):
            result.unsupported_checks.add(item_id)
            result.unsupported_reasons[item_id] = "Device rejected the SSH command as unsupported"

    for metric_key, extract in extractors.items():
        item_id = METRIC_ITEMS[metric_key]
        commands = [
            command for command, items in normalized_items.items()
            if item_id in items and command in sections and command not in unsupported_commands
        ]
        if not commands:
            continue
        if item_id == "storage":
            storage_values = []
            storage_commands = []
            for command in commands:
                candidate = extract(_scoped_output([command], sections))
                if candidate is not None:
                    storage_values.append(candidate)
                    storage_commands.append(command)
            value = _merge_storage_values(storage_values)
            if value is not None:
                result.metrics[metric_key] = value
                result.item_references.setdefault(item_id, storage_commands[0])
            continue
        value = None
        for command in commands:
            candidate = extract(_scoped_output([command], sections))
            if candidate is not None:
                value = candidate
                result.item_references.setdefault(item_id, command)
                break

        if item_id == "active_alarms" and isinstance(value, dict) and not value.get("total", 0):
            result.item_messages[item_id] = "No active device alarms"
        elif item_id == "link_aggregation" and any(
            re.search(r"(?i)\bno valid trunk\b", sections[command]) for command in commands
        ):
            value = value or {"groups": 0, "degraded": 0}
            result.item_messages[item_id] = "No link aggregation is configured"
        elif item_id == "stp" and value is None and any(
            STP_DISABLED.search(sections[command]) for command in commands
        ):
            value = 0
            result.metrics["stp_enabled"] = False
            result.item_messages[item_id] = "STP is not enabled"
        elif item_id == "routing_neighbors" and value is None and all(
            _effectively_empty(sections[command]) for command in commands
        ):
            value = {"total": 0, "established": 0}
            result.item_messages[item_id] = "OSPF is not configured or has no neighbors"

        if value is not None:
            result.metrics[metric_key] = value

    configuration_commands = [
        command for command, items in normalized_items.items()
        if "configuration_saved" in items and command in sections and command not in unsupported_commands
    ]
    if (
        configuration_commands
        and "configuration_saved" not in result.metrics
        and CONFIG_STARTUP_FILE_HINT.search(_scoped_output(configuration_commands, sections))
    ):
        result.unsupported_checks.add("configuration_saved")
        result.unsupported_reasons["configuration_saved"] = (
            "Startup configuration file presence does not prove the current configuration is saved"
        )

    device_commands = [
        command for command, items in normalized_items.items()
        if "device_information" in items and command in sections and command not in unsupported_commands
    ]
    if device_commands:
        device_output = _scoped_output(device_commands, sections)
        result.metrics.update(parse_device_identity(vendor, device_output))

    interface_commands = [
        command for command, items in normalized_items.items()
        if "interfaces" in items and command in sections and command not in unsupported_commands
    ]
    if interface_commands:
        interface_summary = _interface_summary(_scoped_output(interface_commands, sections))
        if interface_summary:
            result.metrics.update(interface_summary)

    if vendor == "Huawei" and "display alarm active" in sections:
        alarm_output = sections["display alarm active"]
        for item_id, pattern in HARDWARE_ALARMS.items():
            alarm_details = _hardware_alarm_details(alarm_output, pattern)
            alarm_count = len(alarm_details)
            hardware = result.metrics.get(item_id)
            if alarm_count and isinstance(hardware, dict):
                hardware["abnormal"] = max(
                    hardware.get("abnormal", 0),
                    min(hardware.get("total", alarm_count), alarm_count),
                )
                components = hardware.get("components") or []
                unmatched_alarms = []
                for alarm in alarm_details:
                    component = _alarm_component(alarm, components)
                    if component is None:
                        unmatched_alarms.append(alarm)
                        continue
                    component["status"] = "abnormal"
                    component.setdefault("alarms", []).append(alarm)
                if unmatched_alarms:
                    hardware["alarms"] = unmatched_alarms
                result.item_messages[item_id] = f"Active {item_id} alarm detected"

    result.unsupported_checks -= {
        item_id for item_id in result.unsupported_checks if any(
            result.metrics.get(metric_key) is not None
            for metric_key, mapped_item in METRIC_ITEMS.items()
            if mapped_item == item_id
        )
    }
    return result


def parse_ssh_metrics(vendor: str, raw_output: str) -> dict[str, object]:
    extractors = VENDOR_PARSERS[vendor]
    metrics: dict[str, object] = parse_device_identity(vendor, raw_output)

    for key, extract in extractors.items():
        value = extract(raw_output)
        if value is not None:
            metrics[key] = value
    interface_summary = _interface_summary(raw_output)
    if interface_summary:
        metrics.update(interface_summary)
    return metrics


SNMP_IDENTITY_PATTERNS = {
    # "VRP (R) software, Version 5.170 (S5720 V200R011C10SPC600)"
    "Huawei": (
        re.compile(r"\((?P<model>[A-Za-z][\w-]*\d[\w-]*)\s+(?P<version>V\d{3}R\d{3}\w*)\)"),
        None,
    ),
    # "Software Version 7.1.064, Release 3208P10" ... "H3C S5130-28S-EI"
    "H3C": (
        re.compile(r"(?im)^\s*(?:H3C|HPE?)\s+(?P<model>[A-Za-z][\w-]*\d[\w-]*)\s*$"),
        re.compile(r"(?i)\bSoftware\s+Version\s+(?P<version>[\d.]+(?:,?\s*Release\s+\w+)?)"),
    ),
    # "Ruijie ... Switch(S5750-28GT4XS-H) By Ruijie Networks" ... "RGOS 11.4(1)B12P"
    "Ruijie": (
        re.compile(r"\((?P<model>[A-Za-z][\w-]*\d[\w-]*)\)"),
        re.compile(r"\bRGOS\s+(?P<version>\d[\w.()-]*)"),
    ),
    # "Cisco IOS Software, C2960X Software (...), Version 15.2(7)E2, RELEASE SOFTWARE"
    "Cisco": (
        None,
        re.compile(r"(?i)\bVersion\s+(?P<version>\d[^,\s]*)"),
    ),
}


def parse_snmp_identity(vendor: str, sys_description: str | None) -> dict[str, str]:
    """Model and software version that a vendor publishes inside sysDescr."""
    if not sys_description or vendor not in SNMP_IDENTITY_PATTERNS:
        return {}
    model_pattern, version_pattern = SNMP_IDENTITY_PATTERNS[vendor]
    identity: dict[str, str] = {}
    model_match = model_pattern.search(sys_description) if model_pattern else None
    if model_match:
        identity["model"] = model_match.group("model")
        if "version" in model_pattern.groupindex:
            identity["software_version"] = model_match.group("version")
    # Some VRP releases put the full model (S5720-32X-EI-AC) on the first line.
    first_line = sys_description.strip().splitlines()[0].strip() if sys_description.strip() else ""
    if (
        identity.get("model")
        and re.fullmatch(r"[A-Za-z][\w-]*\d[\w-]*", first_line)
        and first_line.startswith(identity["model"])
    ):
        identity["model"] = first_line
    version_match = version_pattern.search(sys_description) if version_pattern else None
    if version_match and "software_version" not in identity:
        identity["software_version"] = version_match.group("version").strip()
    return identity
