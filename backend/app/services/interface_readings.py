"""Interface counters and states, preserving field meaning and port identity."""
import re


INTERFACE_NAME = re.compile(r"^([A-Za-z][A-Za-z-]*\d[\w/.:_-]*)(?=\s|$)")
LABEL = r"input\s+errors|output\s+errors|CRC(?:\s+errors)?|input\s+discards|output\s+discards|discards|errors"
COUNTER_BEFORE = re.compile(rf"(?i)(?<![\w.+-])(\d[\d,]*)\s+({LABEL})\b")
COUNTER_AFTER = re.compile(rf"(?i)\b({LABEL})\s*:?\s*(\d[\d,]*)(?![\d.])(?:\s|$)")
UP_STATES = {"up", "connected", "monitoring"}
DOWN_STATES = {
    "down", "*down", "^down", "#down", "adm", "stby", "notconnect", "disabled",
    "err-disabled", "inactive", "sfpabsent", "notpresent", "suspended",
}
ADMIN_STATES = {"*down", "adm", "disabled"}


def interface_error_details(text: str) -> list[dict] | None:
    ports = {}
    name = "unknown"
    direction = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("$"):
            name, direction = "unknown", None
            continue
        interface = INTERFACE_NAME.match(stripped)
        if interface:
            name = interface.group(1)
            direction = None
        direct = re.match(r"(?i)^(Input|Output)\s*:", stripped)
        if direct:
            direction = direct.group(1).lower()
        # A comma separates counters, except when it is a thousands separator.
        for segment in re.split(r",(?!\d{3}(?:\D|$))", stripped):
            match = COUNTER_BEFORE.search(segment)
            if match:
                count, label = match.groups()
            else:
                match = COUNTER_AFTER.search(segment)
                if not match:
                    continue
                label, count = match.groups()
            label = " ".join(label.lower().split())
            if label.startswith("crc"):
                key = "crc_errors"
            elif label in {"errors", "discards"} and direction:
                key = f"{direction}_{label}"
            else:
                key = label.replace(" ", "_")
            port = ports.setdefault(name, {"name": name})
            # Duplicate output for one port must not count the same counter twice.
            port[key] = max(port.get(key, 0), int(count.replace(",", "")))
    details = list(ports.values())
    for port in details:
        if not any(key in port for key in ("input_errors", "output_errors", "crc_errors", "errors")):
            continue
        # CRC is a subcounter of input errors in verbose interface output.
        port["total_errors"] = (
            port.get("input_errors", port.get("crc_errors", 0))
            + port.get("output_errors", 0) + port.get("errors", 0)
        )
    return details or None


def interface_error_count(text: str) -> int | None:
    details = interface_error_details(text)
    if not details or not any(any(key in port for key in (
        "input_errors", "output_errors", "crc_errors", "errors",
    )) for port in details):
        return None
    return sum(port.get("total_errors", 0) for port in details)


def interface_discard_count(text: str) -> int | None:
    details = interface_error_details(text)
    keys = ("input_discards", "output_discards", "discards")
    if not details or not any(any(key in port for key in keys) for port in details):
        return None
    return sum(port.get(key, 0) for port in details for key in keys)


SUMMARY_COMMANDS = ("display interface brief", "show interfaces status", "show ip interface brief")


def _ip_interface_brief_row(fields: list[str]) -> dict | None:
    """Cisco "show ip interface brief": ... Method Status Protocol."""
    if len(fields) < 6:
        return None
    protocol = fields[-1].lower()
    if [field.lower() for field in fields[-3:-1]] == ["administratively", "down"]:
        status, admin_down = "down", True
    else:
        status, admin_down = fields[-2].lower(), False
    if status not in {"up", "down"} or protocol not in {"up", "down"}:
        return None
    return {
        "name": fields[0], "physical_status": status, "protocol_status": protocol,
        "admin_down": admin_down, "error_disabled": False,
    }


def interface_summary(text: str) -> dict | None:
    rows_by_command: dict[str, list[dict]] = {}
    command = ""
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("$"):
            command = stripped[1:].strip().lower()
            continue
        if command not in SUMMARY_COMMANDS:
            continue
        fields = stripped.split()
        if len(fields) < 2 or not INTERFACE_NAME.fullmatch(fields[0]):
            continue
        rows = rows_by_command.setdefault(command, [])
        if command == "show ip interface brief":
            row = _ip_interface_brief_row(fields)
            if row:
                rows.append(row)
            continue
        protocol = None
        if command == "show interfaces status":
            # Name/description is optional and may itself be "down". Status is
            # identified by the VLAN/duplex/speed columns that follow it. Use
            # the final structural match so description text cannot win.
            state = None
            candidate_indexes = []
            for index in range(1, len(fields) - 3):
                vlan = fields[index + 1].lower()
                duplex = fields[index + 2].lower()
                speed = fields[index + 3].lower()
                valid_speed = bool(re.fullmatch(r"(?:a-)?(?:auto|\d+(?:\.\d+)?[gmk]?)|--", speed))
                if ((vlan.isdigit() or vlan in {"trunk", "routed", "unassigned"})
                        and duplex in {"auto", "full", "half", "a-full", "a-half", "--"}
                        and valid_speed):
                    candidate_indexes.append(index)
            if candidate_indexes:
                state = fields[candidate_indexes[-1]].lower()
        else:
            # VRP appends cause flags such as up(s), down(e) or down(dl) to the
            # physical state; the flag explains the state but is not part of it.
            state = re.sub(r"\([^)]*\)$", "", fields[1].lower())
            if len(fields) > 2:
                candidate = re.sub(r"\([^)]*\)", "", fields[2]).lower()
                if candidate in UP_STATES | DOWN_STATES:
                    protocol = "up" if candidate in UP_STATES else "down"
        if state is None:
            continue
        physical_status = "up" if state in UP_STATES else "down" if state in DOWN_STATES else "unknown"
        rows.append({
            "name": fields[0], "physical_status": physical_status,
            "protocol_status": protocol, "admin_down": state in ADMIN_STATES,
            "error_disabled": state == "err-disabled",
        })
    # Commands name ports differently (Gi1/0/1 vs GigabitEthernet1/0/1), so
    # the first command that produced rows is the only source of the summary.
    rows = next((rows_by_command[name] for name in SUMMARY_COMMANDS if rows_by_command.get(name)), [])
    if not rows:
        return None
    # One port can be shown in both route/bridge subsections of an output.
    rows = list({row["name"]: row for row in rows}.values())
    up = sum(row["physical_status"] == "up" for row in rows)
    down = sum(row["physical_status"] == "down" for row in rows)
    return {
        "interface_collection_status": "success", "interfaces_total": len(rows),
        "interfaces_up": up, "interfaces_down": down,
        "interfaces_admin_down": sum(row["admin_down"] for row in rows),
        "interfaces_error_disabled": sum(row["error_disabled"] for row in rows),
        "interfaces_unknown": len(rows) - up - down,
        "interface_details": rows,
    }
