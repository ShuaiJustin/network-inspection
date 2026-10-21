"""Bounded CPU/memory formats with explicit object and sampling-window identity."""
import re


NUMBER = r"[+-]?\d+(?:\.\d+)?"
WINDOW = r"(?:five|one|5|1)\s*(?:seconds?|secs?|minutes?|mins?)"


def _percent(value: str) -> int | float | None:
    if not re.fullmatch(NUMBER, value.strip()):
        return None
    number = float(value)
    if not 0 <= number <= 100:
        return None
    return int(number) if number.is_integer() else number


def _window(value: str) -> str:
    normalized = value.lower().replace("five", "5").replace("one", "1")
    number = re.search(r"\d+", normalized).group()
    return number + ("s" if "sec" in normalized else "m")


def _object_id(line: str, previous: str) -> str:
    slot = re.search(r"(?i)\b(?:slot|slotid)\s*[:#]?\s*(\d+)\b", line)
    cpu = re.search(r"(?i)\bcpu\s*[:#]?\s*(\d+)\b", line)
    if slot:
        previous = f"slot:{slot.group(1)}"
    if cpu:
        previous = previous.split("/cpu:")[0]
        previous = f"{previous}/cpu:{cpu.group(1)}" if previous != "system" else f"cpu:{cpu.group(1)}"
    return previous


def _reading(object_id, percent, window="current", scope="overall"):
    return {"object_id": object_id, "scope": scope, "window": window, "percent": percent}


def cpu_readings(vendor: str, text: str) -> list[dict] | None:
    readings = []
    object_id = "system"
    table_columns = []
    table_windows = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("$"):
            object_id, table_columns, table_windows = "system", [], []
            continue
        object_id = _object_id(line, object_id)
        if vendor == "H3C":
            windows = list(re.finditer(rf"(?i)last\s*({WINDOW})", line))
            if windows and "%" not in line:
                table_columns = line[:windows[0].start()].lower().split()
                table_windows = [_window(match.group(1)) for match in windows]
                continue
            if table_windows:
                fields = line.split()
                if len(fields) == len(table_columns) + len(table_windows):
                    identities = fields[:len(table_columns)]
                    if identities and all(value.isdigit() for value in identities):
                        row_id = "/".join(f"{key}:{value}" for key, value in zip(table_columns, identities))
                        for window, value in zip(table_windows, fields[len(table_columns):]):
                            percent = _percent(value[:-1]) if value.endswith("%") else None
                            if percent is not None:
                                readings.append(_reading(row_id, percent, window))
                        continue
            match = re.fullmatch(rf"({NUMBER})\s*%\s+in\s+last\s+({WINDOW})\s*", line, re.I)
            if match:
                percent = _percent(match.group(1))
                if percent is not None:
                    readings.append(_reading(object_id, percent, _window(match.group(2))))
                continue

        # IOS's second percentage is interrupt utilisation, not a second CPU.
        if vendor in {"Cisco", "Huawei", "Ruijie", "H3C"}:
            matches = list(re.finditer(
                rf"(?i)(?:CPU utilization for\s+)?({WINDOW})\s*:\s*({NUMBER})\s*%"
                rf"(?:\s*/\s*{NUMBER}\s*%)?",
                line,
            )) if re.match(r"(?i)^CPU utilization for\b", line) else []
            if matches:
                for match in matches:
                    percent = _percent(match.group(2))
                    if percent is not None:
                        readings.append(_reading(object_id, percent, _window(match.group(1))))
                continue
        if vendor != "Cisco":
            match = re.fullmatch(
                rf"(?i)(?:(management-plane|data-plane)\s+)?CPU\s+"
                rf"(?:usage|utilization)\s*:?\s*({NUMBER})\s*%"
                rf"(?:\s+Max\s*:\s*{NUMBER}\s*%)?",
                line,
            )
            if match:
                percent = _percent(match.group(2))
                if percent is not None:
                    readings.append(_reading(object_id, percent, scope=(match.group(1) or "overall").lower()))
    return readings or None


def memory_readings(vendor: str, text: str) -> list[dict] | None:
    readings = []
    object_id = "system"
    memory_table = False
    scope = "overall"
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("$"):
            object_id, memory_table, scope = "system", False, "overall"
            continue
        if re.match(r"(?i)^Container memory statistics\b", line):
            scope = "container"
        elif re.match(r"(?i)^Memory statistics\b", line):
            scope = "overall"
        object_id = _object_id(line, object_id)
        percent = None
        if vendor == "Huawei":
            match = re.fullmatch(rf"(?i)Memory Using Percentage(?:\s+Is)?\s*:\s*({NUMBER})\s*%", line)
            if match:
                percent = _percent(match.group(1))
        elif vendor in {"H3C", "Ruijie"}:
            match = re.fullmatch(rf"(?i)Used\s*Rate\s*:\s*({NUMBER})\s*%", line)
            if match:
                percent = _percent(match.group(1))
            if vendor == "H3C":
                match = re.match(r"(?i)^Mem:\s*(\d+)\s+(\d+)\s+(\d+)(?:\s|$)", line)
                if match:
                    total, used, free = map(int, match.groups())
                    if total > 0 and 0 <= used <= total and 0 <= free <= total:
                        percent = round(used / total * 100)
            if re.search(r"(?i)\bTotal\b.*\bUsed\b.*\bFree\b.*Used\s*Rate\b", line):
                memory_table = True
                continue
            if memory_table:
                match = re.fullmatch(rf"(\d+)\s+(\d+)\s+(\d+)\s+({NUMBER})\s*%", line)
                if match:
                    total, used, free = map(int, match.groups()[:3])
                    if total > 0 and used <= total and free <= total:
                        percent = _percent(match.group(4))
        elif vendor == "Cisco":
            match = re.search(r"(?i)Processor Pool Total:\s*(\d+)\s+Used:\s*(\d+)\b", line)
            if match:
                total, used = map(int, match.groups())
                if total > 0 and used <= total:
                    percent = round(used / total * 100)
        if percent is not None:
            readings.append(_reading(object_id, percent, scope=scope))
    return readings or None


def resource_percent(readings: list[dict] | None) -> int | float | None:
    if not readings:
        return None
    # Overall and per-plane measurements overlap. Prefer overall if available.
    candidates = [reading for reading in readings if reading["scope"] == "overall"] or readings
    # Never compare peaks from unrelated sampling windows.
    window = next((value for value in ("5s", "current", "1m", "5m") if any(
        reading["window"] == value for reading in candidates
    )), candidates[0]["window"])
    return max(reading["percent"] for reading in candidates if reading["window"] == window)
