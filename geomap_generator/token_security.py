"""
OS-native token storage for API keys.

  macOS   — system Keychain via `security` subprocess
  Windows — DPAPI (CryptProtectData), machine-bound real encryption via ctypes
  Linux   — mode-600 JSON file in Blender config dir

Legacy XOR-encrypted values (prefix "enc:v1:") are decrypted transparently
for backward compatibility, but never written in the old format.
"""

import json
import os
import sys

_SERVICE = "geomap_generator"
_SENTINEL = "OS_KEYCHAIN_V2"
_LEGACY_PREFIX = "enc:v1:"


# ── public API ──────────────────────────────────────────────────────────────

def encrypt_token(value: str, service_name: str) -> str:
    """Store token in OS keychain; return sentinel for Blender preferences."""
    if not value:
        return ""
    try:
        _os_store(service_name, value)
    except Exception:
        pass
    return _SENTINEL


def decrypt_token(stored: str, service_name: str) -> str:
    """Return plaintext token. Reads OS keychain or falls back to legacy XOR."""
    if not stored:
        return ""
    if stored == _SENTINEL:
        try:
            return _os_retrieve(service_name) or ""
        except Exception:
            return ""
    if stored.startswith(_LEGACY_PREFIX):
        return _xor_decrypt(stored)
    return stored


def has_encrypted_token(stored: str) -> bool:
    return bool(stored)


# ── OS dispatch ──────────────────────────────────────────────────────────────

def _os_store(service_name: str, value: str) -> None:
    if sys.platform == "darwin":
        _mac_store(service_name, value)
    elif sys.platform == "win32":
        _win_store(service_name, value)
    else:
        _file_store(service_name, value)


def _os_retrieve(service_name: str) -> str | None:
    if sys.platform == "darwin":
        return _mac_retrieve(service_name)
    if sys.platform == "win32":
        return _win_retrieve(service_name)
    return _file_retrieve(service_name)


# ── macOS: system Keychain via `security` CLI ────────────────────────────────

def _mac_store(service_name: str, value: str) -> None:
    import subprocess
    subprocess.run(
        ["security", "delete-generic-password", "-s", _SERVICE, "-a", service_name],
        capture_output=True,
    )
    subprocess.run(
        ["security", "add-generic-password", "-s", _SERVICE, "-a", service_name, "-w", value],
        capture_output=True,
        check=True,
    )


def _mac_retrieve(service_name: str) -> str | None:
    import subprocess
    result = subprocess.run(
        ["security", "find-generic-password", "-s", _SERVICE, "-a", service_name, "-w"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() if result.returncode == 0 else None


# ── Windows: DPAPI via ctypes (machine-bound encryption) ────────────────────

def _win_store(service_name: str, value: str) -> None:
    import base64
    import ctypes
    import ctypes.wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    data = value.encode("utf-8")
    buf = (ctypes.c_byte * len(data))(*data)
    blob_in = _BLOB(len(data), buf)
    blob_out = _BLOB()

    if not ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        raise ctypes.WinError()

    encrypted = bytes(blob_out.pbData[: blob_out.cbData])
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    _file_store(service_name, base64.b64encode(encrypted).decode("ascii"), suffix=".win")


def _win_retrieve(service_name: str) -> str | None:
    import base64
    import ctypes
    import ctypes.wintypes

    b64 = _file_retrieve(service_name, suffix=".win")
    if not b64:
        return None

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", ctypes.c_uint), ("pbData", ctypes.POINTER(ctypes.c_byte))]

    try:
        encrypted = base64.b64decode(b64.encode("ascii"))
    except Exception:
        return None

    buf = (ctypes.c_byte * len(encrypted))(*encrypted)
    blob_in = _BLOB(len(encrypted), buf)
    blob_out = _BLOB()

    if not ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(blob_in), None, None, None, None, 0, ctypes.byref(blob_out)
    ):
        return None

    result = bytes(blob_out.pbData[: blob_out.cbData]).decode("utf-8", errors="replace")
    ctypes.windll.kernel32.LocalFree(blob_out.pbData)
    return result


# ── Linux / fallback: mode-600 JSON file ─────────────────────────────────────

def _token_file(suffix: str = "") -> str:
    try:
        import bpy
        base = bpy.utils.user_resource("CONFIG", path="geomap_generator", create=True)
    except Exception:
        base = os.path.expanduser("~/.config/geomap_generator")
    return os.path.join(base, f"tokens{suffix}.json")


def _file_store(service_name: str, value: str, suffix: str = "") -> None:
    path = _token_file(suffix)
    try:
        with open(path) as f:
            data = json.load(f)
    except Exception:
        data = {}
    data[service_name] = value
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)
    try:
        os.chmod(path, 0o600)
    except Exception:
        pass


def _file_retrieve(service_name: str, suffix: str = "") -> str | None:
    try:
        with open(_token_file(suffix)) as f:
            return json.load(f).get(service_name)
    except Exception:
        return None


# ── Legacy XOR decrypt (read-only backward compat) ───────────────────────────

def _xor_decrypt(value: str) -> str:
    import base64
    import hashlib
    import platform

    seed = "|".join((
        platform.node(), platform.system(), platform.machine(),
        os.path.expanduser("~"), "geomap_generator",
    ))
    key = hashlib.sha256(seed.encode("utf-8")).digest()
    try:
        data = base64.urlsafe_b64decode(value[len(_LEGACY_PREFIX):].encode("ascii"))
        return bytes(b ^ key[i % len(key)] for i, b in enumerate(data)).decode("utf-8")
    except Exception:
        return ""
