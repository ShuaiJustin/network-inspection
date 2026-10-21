import base64
import ctypes
import os
import re
from ctypes import POINTER, Structure, byref, c_char, cast

from argon2 import PasswordHasher


_password_hasher = PasswordHasher()
_SENSITIVE_CONFIG_VALUE = re.compile(
    r"(?im)(\b(?:password|secret|community|auth(?:entication)?(?:-key|-password)?|priv(?:acy)?(?:-key|-password)?)\b)[^\r\n]*"
)
_SENSITIVE_RUNTIME_VALUE = re.compile(
    r"(?i)\b(password|secret|community|auth(?:entication)?[-_ ](?:key|password)|priv(?:acy)?[-_ ](?:key|password))\b\s*(?:=|:|\s+)\s*(?:\"[^\"]*\"|'[^']*'|\S+)"
)
_SENSITIVE_COMMAND_VALUES = (
    re.compile(r"(?im)^(\s*(?:tacacs-server|radius-server)\s+key\s+(?:(?:0|7)\s+)?)(\S+)"),
    re.compile(r"(?im)^(\s*ip\s+ospf\s+message-digest-key\s+\d+\s+md5\s+(?:(?:0|7)\s+)?)(\S+)"),
    re.compile(r"(?im)(\bkey-string\s+)(\S+)"),
    re.compile(r"(?im)(\blocal-user\b[^\r\n]*\bkey\s+[07]\s+)(\S+)"),
    re.compile(r"(?im)(\bradius-server\s+shared-key\s+)(\S+)"),
    re.compile(r"(?im)(\bcrypto\s+isakmp\s+key\s+)(\S+)"),
    re.compile(
        r"(?im)(\bsnmp-server\s+host\s+\S+(?:\s+traps)?(?:\s+version\s+\S+)?\s+)(\S+)"
    ),
    re.compile(r"(?im)(\b(?:pre-shared-key|preshared-key|pass-phrase|passphrase|psk)\b\s+)([^\r\n]*)"),
)
_AAA_BLOCK_HEADER = re.compile(
    r"(?i)^(?:tacacs|tacacs-server|hwtacacs|hwtacacs-server|radius|radius-server)"
    r"\s+(?:server|template|scheme)\b"
)
_AAA_BLOCK_KEY = re.compile(r"(?i)^(\s*key\s+(?:(?:0|7)\s+)?)(\S+)(.*)$")


class _DataBlob(Structure):
    _fields_ = [("cbData", ctypes.c_uint), ("pbData", POINTER(c_char))]


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    if os.name != "nt":
        raise RuntimeError("Credential encryption requires Windows DPAPI")
    raw = value.encode("utf-8")
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = _DataBlob(len(raw), cast(in_buffer, POINTER(c_char)))
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptProtectData(byref(in_blob), None, None, None, None, 0, byref(out_blob)):
        raise ctypes.WinError()
    try:
        return base64.b64encode(ctypes.string_at(out_blob.pbData, out_blob.cbData)).decode("ascii")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return None
    if os.name != "nt":
        raise RuntimeError("Credential decryption requires Windows DPAPI")
    raw = base64.b64decode(value)
    in_buffer = ctypes.create_string_buffer(raw)
    in_blob = _DataBlob(len(raw), cast(in_buffer, POINTER(c_char)))
    out_blob = _DataBlob()
    if not ctypes.windll.crypt32.CryptUnprotectData(byref(in_blob), None, None, None, None, 0, byref(out_blob)):
        raise ctypes.WinError()
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(out_blob.pbData)


class CredentialDecryptionError(RuntimeError):
    """Raised when a stored credential cannot be decrypted on this machine."""


def decrypt_secret_or_fail(value: str | None) -> str | None:
    """Decrypt a stored credential, converting low-level failures into a clear error.

    DPAPI blobs are bound to the Windows user that created them, so a database
    restored under a different user (or a corrupted value) cannot be decrypted.
    The raw binascii/OSError must never reach the collectors.
    """
    try:
        return decrypt_secret(value)
    except Exception as exc:
        raise CredentialDecryptionError(
            "Stored credential could not be decrypted on this Windows user; "
            "please re-enter the device credentials"
        ) from exc


def hash_admin_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_admin_password(password_hash: str, password: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except Exception:
        return False


def _redact_aaa_block_keys(value: str) -> str:
    """Redact bare key directives only while inside a TACACS/RADIUS block."""
    redacted: list[str] = []
    in_aaa_block = False
    for line in value.splitlines(keepends=True):
        body = line.rstrip("\r\n")
        ending = line[len(body):]
        stripped = body.strip()
        if _AAA_BLOCK_HEADER.match(stripped):
            in_aaa_block = True
        elif in_aaa_block:
            key_match = _AAA_BLOCK_KEY.match(body)
            if key_match:
                body = f"{key_match.group(1)}[REDACTED]{key_match.group(3)}"
            elif stripped in {"!", "#", "quit", "return"}:
                in_aaa_block = False
            elif stripped and not body[:1].isspace():
                in_aaa_block = False
        redacted.append(body + ending)
    return "".join(redacted)


def redact_configuration_text(value: str) -> str:
    redacted = _redact_aaa_block_keys(value)
    for pattern in _SENSITIVE_COMMAND_VALUES:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return _SENSITIVE_CONFIG_VALUE.sub(
        lambda match: f"{match.group(1)} [REDACTED]", redacted
    )


def redact_runtime_text(value: str) -> str:
    redacted = _redact_aaa_block_keys(value)
    for pattern in _SENSITIVE_COMMAND_VALUES:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return _SENSITIVE_RUNTIME_VALUE.sub(
        lambda match: f"{match.group(1)} [REDACTED]", redacted
    )
