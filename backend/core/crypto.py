# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (c) 2025-2026 ViralMint Contributors
"""
Symmetric encryption using Fernet (AES-128-CBC + HMAC-SHA256).
All secrets stored in DB must be encrypted with these functions.
"""
from cryptography.fernet import Fernet
from backend.config import settings


def _get_fernet() -> Fernet:
    key = settings.ENCRYPTION_KEY
    if not key:
        raise RuntimeError(
            "ENCRYPTION_KEY is not set. This should have been auto-generated on startup. "
            "Check .env file or backend/config.py _ensure_secrets()."
        )
    return Fernet(key.encode() if isinstance(key, str) else key)


def encrypt(plaintext: str) -> str:
    """Encrypt a string. Returns base64-encoded encrypted string."""
    if not plaintext:
        return ""
    f = _get_fernet()
    return f.encrypt(plaintext.encode()).decode()


class DecryptionError(Exception):
    """Raised when decryption fails due to key change or data corruption."""
    pass


def decrypt(ciphertext: str) -> str:
    """Decrypt an encrypted string. Returns plaintext.
    Raises DecryptionError if the key has changed or data is corrupted.
    """
    if not ciphertext:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode()).decode()
    except Exception as e:
        raise DecryptionError(
            f"Failed to decrypt value — encryption key may have changed or data is corrupted: {e}"
        )


def decrypt_safe(ciphertext: str) -> str:
    """Decrypt an encrypted string, returning empty string on failure.
    Use this for non-critical paths (e.g. cookie fallbacks) where a corrupted
    value should not crash the entire operation.
    """
    if not ciphertext:
        return ""
    try:
        f = _get_fernet()
        return f.decrypt(ciphertext.encode()).decode()
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Decryption failed (key changed or data corrupted): {e}")
        return ""


def mask(value: str, show_last: int = 4) -> str:
    """Mask a secret for display — shows only last N chars."""
    if not value or len(value) <= show_last:
        return "****"
    return "*" * (len(value) - show_last) + value[-show_last:]
