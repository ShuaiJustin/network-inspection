"""Conservative integrity checks for complete configuration command output.

This validates the original output without changing the text persisted by the
caller. Supported families use their own configuration shape and terminator;
an unrecognized platform does not silently inherit another vendor's rules.
"""

import re

from app.services.vendor_registry import PLATFORMS, resolve_platform


_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ERROR_ROW = re.compile(
    r"(?i)^(?:(?:%\s*)?(?:error\s*:|invalid (?:input|command)|incomplete command|"
    r"ambiguous command|unknown command|unrecognized command|command not found|"
    r"permission denied|authorization (?:failed|failure)|access denied)|"
    r"(?:%\s*)?(?:info\s*:\s*)?(?:you (?:do not|don't) have permission|"
    r"permission denied|authorization (?:failed|failure)|access denied|"
    r"(?:this |the )?command\b.*\b(?:not supported|unsupported|not permitted|not authorized|authorization failed)|"
    r"unsupported command))"
)
_PAGER_ROW = re.compile(
    r"(?i)^(?:-{2,}\s*more\s*(?:\([^\n]*\)\s*)?-{2,}"
    r"(?:\s*(?:\(.*\)|\[.*\]|press\b.*))?|"
    r"press (?:any key|space|<space>|enter|return) to continue[.\s]*)\s*$"
)
_TRUNCATION_ROW = re.compile(
    r"(?i)^(?:%\s*)?(?:\.{3}\s*)?(?:\[\s*|<\s*)?"
    r"(?:output|configuration)\s+(?:(?:is|was)\s+)?truncated\b"
)
_BANNER_START = re.compile(
    r"(?i)^(?:banner\s+(?:exec|incoming|login|motd|prompt-timeout|slip-ppp)|"
    r"header\s+(?:login|shell|legal)(?:\s+information)?)\s+(.+)$"
)
_PROMPT = re.compile(r"^(?:<[^<>\s]+>|\[[^\[\]\s]+\]|[\w.:-]+(?:\([^\n()]+\))?[#>])$")

# These are configuration statements, not arbitrary prose, comments, command
# echoes or device banners. A hostname and terminator alone are not a backup.
_VRP_STRUCTURE = re.compile(
    r"(?i)^(?:interface\s+\S+|vlan(?:\s+batch)?\s+\S+|aaa(?:\s|$)|"
    r"snmp-agent(?:\s|$)|local-user\s+\S+|user-interface\s+\S+|"
    r"(?:ip|ipv6|stp|ospf|bgp|isis|acl|dhcp|ntp-service|ntp|ssh|stelnet|"
    r"telnet|http|pki|rsa|undo)\s+\S+)"
)
_IOS_STRUCTURE = re.compile(
    r"(?i)^(?:interface\s+\S+|vlan\s+\S+|line\s+\S+|"
    r"(?:ip|ipv6|router|aaa|username|enable|snmp-server|spanning-tree|"
    r"ntp|service|no|boot|crypto|logging|access-list)\s+\S+)"
)
_PLATFORM_FAMILIES = {
    "huawei-vrp": "vrp",
    "h3c-comware": "vrp",
    "cisco-ios": "ios",
    "ruijie-rgos": "ios",
}


def validate_configuration(
    output: str,
    vendor: str | None = None,
    platform_id: str | None = None,
) -> str | None:
    """Return a safe rejection reason, or None for a complete configuration.

    Pass vendor and platform_id for a device. Legacy callers omitting both get
    shape inference (sysname/return or hostname/end), never a length-only check.
    The return/end line must be the final configuration line, allowing a final
    device prompt. Delimited banner/header contents are treated as literal text.
    """
    if not isinstance(output, str) or len(output.strip()) < 20:
        return "Configuration output is empty or truncated"

    lines = []
    banner_delimiter = None
    normalized = _ANSI_ESCAPE.sub("", output).replace("\r\n", "\n").replace("\r", "\n")
    for raw_line in normalized.split("\n"):
        line = raw_line.replace("\b", "").strip()
        if banner_delimiter is not None:
            if banner_delimiter in line:
                banner_delimiter = None
            continue
        banner = _BANNER_START.match(line)
        if banner:
            content = banner.group(1)
            delimiter = content[:2] if content.startswith("^") and len(content) > 1 else content[0]
            if delimiter not in content[len(delimiter):]:
                banner_delimiter = delimiter
            # Keep a non-matching sentinel so a banner after end/return cannot
            # turn an earlier terminator into the final configuration line.
            lines.append("<configuration banner>")
            continue
        if _ERROR_ROW.match(line):
            return "Configuration output contains a device error message"
        if _PAGER_ROW.fullmatch(line):
            return "Configuration output contains a paging marker and may be incomplete"
        if _TRUNCATION_ROW.match(line):
            return "Configuration output contains a truncated output notice"
        if line:
            lines.append(line)
    if banner_delimiter is not None:
        return "Configuration output contains an unterminated banner and is truncated"

    if vendor is not None:
        try:
            platform = resolve_platform(vendor, platform_id)
        except ValueError:
            return "Configuration validation does not support the selected platform"
        family = _PLATFORM_FAMILIES.get(platform.id)
    elif platform_id is not None:
        family = _PLATFORM_FAMILIES.get(platform_id) if platform_id in PLATFORMS else None
    else:
        family = (
            "vrp" if any(re.match(r"(?i)^sysname\s+\S+", line) for line in lines)
            else "ios" if any(re.match(r"(?i)^hostname\s+\S+", line) for line in lines)
            else None
        )
    if family is None:
        return "Configuration output has no recognized configuration structure for this platform"

    identity = r"(?i)^sysname\s+\S+" if family == "vrp" else r"(?i)^hostname\s+\S+"
    structure = _VRP_STRUCTURE if family == "vrp" else _IOS_STRUCTURE
    if not any(re.match(identity, line) for line in lines) or not any(structure.match(line) for line in lines):
        return "Configuration output is missing the expected configuration structure"
    while lines and _PROMPT.fullmatch(lines[-1]):
        lines.pop()
    terminator = "return" if family == "vrp" else "end"
    if not lines or lines[-1].lower() != terminator:
        return f"Configuration output is truncated or missing its final {terminator} marker"
    return None
