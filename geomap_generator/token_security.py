import base64
import hashlib
import os
import platform

_PREFIX = "enc:v1:"


def encrypt_token(value: str) -> str:
    if not value:
        return ""
    data = value.encode("utf-8")
    key = _machine_key()
    encrypted = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    return _PREFIX + base64.urlsafe_b64encode(encrypted).decode("ascii")


def decrypt_token(value: str) -> str:
    if not value:
        return ""
    if not value.startswith(_PREFIX):
        return value
    try:
        data = base64.urlsafe_b64decode(value[len(_PREFIX) :].encode("ascii"))
    except Exception:
        return ""
    key = _machine_key()
    decrypted = bytes(byte ^ key[index % len(key)] for index, byte in enumerate(data))
    try:
        return decrypted.decode("utf-8")
    except UnicodeDecodeError:
        return ""


def has_encrypted_token(value: str) -> bool:
    return bool(decrypt_token(value))


def _machine_key() -> bytes:
    seed = "|".join(
        (
            platform.node(),
            platform.system(),
            platform.machine(),
            os.path.expanduser("~"),
            "geomap_generator",
        )
    )
    return hashlib.sha256(seed.encode("utf-8")).digest()
