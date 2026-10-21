import pytest

from app.routers.backup import _validate_backup_output
from app.services.configuration_validation import validate_configuration


HUAWEI_CONFIG = "#\nsysname Core-SW\n#\ninterface GigabitEthernet0/0/1\n description uplink\n#\nreturn\n"
CISCO_CONFIG = "Building configuration...\nCurrent configuration : 120 bytes\n!\nversion 17.9\nhostname Core-SW\n!\ninterface GigabitEthernet1/0/1\n description uplink\n!\nend\n"


@pytest.mark.parametrize("output", [
    "% Permission denied to execute this command.",
    "% Authorization failed for this command.",
    "% Access denied for this command.",
    "Info: You do not have permission to run this command.",
    "Error: Unrecognized command found at '^' position.",
    "% Invalid input detected at '^' marker.",
    "% Command is not supported on this platform.",
    "This command is not supported on this device.",
    "The command is not permitted for this account.",
    "Unknown command: display current-configuration",
])
def test_command_failure_output_is_never_a_configuration(output):
    assert _validate_backup_output(output) is not None


@pytest.mark.parametrize("output", [
    "sysname Core-SW\ninterface GigabitEthernet0/0/1\n---- More ----",
    "hostname Core-SW\ninterface GigabitEthernet1/0/1\n--More--",
    HUAWEI_CONFIG.replace("#\nreturn", "---- More ----\n#\nreturn"),
    CISCO_CONFIG.replace("!\nend", "\x1b[7m--More--\x1b[0m\n!\nend"),
])
def test_paged_output_is_rejected_even_if_a_terminator_follows(output):
    reason = _validate_backup_output(output)
    assert reason is not None
    assert "pag" in reason.lower()


@pytest.mark.parametrize("output", [
    HUAWEI_CONFIG.removesuffix("return\n"),
    CISCO_CONFIG.removesuffix("end\n"),
    HUAWEI_CONFIG.replace("return", "end"),
    CISCO_CONFIG.replace("\nend\n", "\nreturn\n"),
    HUAWEI_CONFIG + "interface GigabitEthernet0/0/2\n",
    "sysname Core-SW\nreturn\n",
    "display current-configuration\nvalid configuration output",
    "Welcome to the network management console. Device ready for commands.",
])
def test_long_fragments_and_non_configuration_text_are_rejected(output):
    assert _validate_backup_output(output) is not None


@pytest.mark.parametrize("output", [
    HUAWEI_CONFIG,
    CISCO_CONFIG,
    "<Core-SW>display current-configuration\n" + HUAWEI_CONFIG + "<Core-SW>\n",
    "Core-SW#show running-config\n" + CISCO_CONFIG + "Core-SW#\n",
    HUAWEI_CONFIG.replace("description uplink", "description Permission denied --More-- Error: test"),
    CISCO_CONFIG.replace("description uplink", "description command not supported ---- More ----"),
    CISCO_CONFIG.replace("!\nend", "banner motd ^C\n% Permission denied to execute this command.\nError: authorized users only\n---- More ----\n^C\n!\nend"),
    HUAWEI_CONFIG.replace("#\nreturn", "header login information %\nError: authorized users only\n---- More ----\n%\n#\nreturn"),
    CISCO_CONFIG.replace("!\nend", "banner motd #Error: authorized users only ---- More ----#\n!\nend"),
])
def test_complete_configuration_preserves_literal_error_and_pager_text(output):
    assert _validate_backup_output(output) is None


def test_banner_cannot_hide_a_missing_configuration_terminator():
    output = CISCO_CONFIG.removesuffix("end\n") + "banner motd ^C\nend\n^C\n"
    assert _validate_backup_output(output) is not None


def test_unfinished_banner_is_rejected_even_if_it_contains_end():
    output = CISCO_CONFIG.removesuffix("end\n") + "banner motd ^C\nUnauthorized access forbidden\nend\n"
    assert _validate_backup_output(output) is not None


@pytest.mark.parametrize("vendor,platform_id,output", [
    ("Huawei", "huawei-vrp", HUAWEI_CONFIG),
    ("H3C", "h3c-comware", HUAWEI_CONFIG),
    ("Cisco", "cisco-ios", CISCO_CONFIG),
    ("Ruijie", "ruijie-rgos", CISCO_CONFIG),
    ("华为", None, HUAWEI_CONFIG),
    (None, "ruijie-rgos", CISCO_CONFIG),
])
def test_shared_validator_uses_supported_platform_rules(vendor, platform_id, output):
    assert validate_configuration(output, vendor, platform_id) is None


@pytest.mark.parametrize("vendor,platform_id,output", [
    ("Huawei", "huawei-vrp", CISCO_CONFIG),
    ("Cisco", "cisco-ios", HUAWEI_CONFIG),
    ("Huawei", "cisco-ios", HUAWEI_CONFIG),
    ("Huawei", "unknown-os", HUAWEI_CONFIG),
    ("Other", None, CISCO_CONFIG),
    (None, "unknown-os", HUAWEI_CONFIG),
])
def test_platform_mismatch_is_rejected_without_guessing_another_os(vendor, platform_id, output):
    assert validate_configuration(output, vendor, platform_id) is not None


@pytest.mark.parametrize("error", [
    "% Permission denied to execute this command.",
    "% Authorization failed for this command.",
    "% Command authorization failed.",
    "% Access denied for this command.",
    "Info: You do not have permission to run this command.",
    "Error: Unrecognized command found at '^' position.",
    "% Invalid input detected at '^' marker.",
    "% Command is not supported on this platform.",
    "This command is not supported on this device.",
    "The command is not permitted for this account.",
    "Unknown command: display current-configuration",
])
def test_complete_shape_does_not_override_an_actual_command_error(error):
    output = CISCO_CONFIG.replace("\nend\n", f"\n{error}\nend\n")
    reason = validate_configuration(output, "Cisco", "cisco-ios")
    assert reason is not None
    assert "error" in reason.lower()


@pytest.mark.parametrize("marker", [
    "[output truncated]",
    "... output truncated ...",
    "% Output truncated to 65535 characters.",
])
def test_explicit_truncation_notice_is_rejected_even_with_a_final_marker(marker):
    output = CISCO_CONFIG.replace("\nend\n", f"\n{marker}\nend\n")
    reason = validate_configuration(output, "Cisco")
    assert reason is not None
    assert "truncated" in reason.lower()


@pytest.mark.parametrize("marker", [
    "---- More ( Press 'Q' to break ) ----",
    "Press any key to continue...",
])
def test_interactive_paging_prompts_are_rejected(marker):
    output = HUAWEI_CONFIG.replace("\nreturn\n", f"\n{marker}\nreturn\n")
    reason = validate_configuration(output, "Huawei")
    assert reason is not None
    assert "pag" in reason.lower()
