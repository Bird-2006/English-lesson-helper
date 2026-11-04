import base64

import pytest

from core import secure_store


def test_plaintext_passthrough_when_not_encrypted():
    assert secure_store.unprotect("plain-password") == "plain-password"
    assert secure_store.unprotect("") == ""


def test_protect_keeps_already_protected_value():
    value = "dpapi:AAAA"
    assert secure_store.protect(value) == value


@pytest.mark.skipif(not secure_store.is_available(), reason="需要 Windows DPAPI")
def test_roundtrip_with_dpapi():
    plain = "hwb-口令-123"
    stored = secure_store.protect(plain)
    assert stored.startswith(secure_store.PREFIX)
    assert stored != plain
    assert secure_store.unprotect(stored) == plain


@pytest.mark.skipif(not secure_store.is_available(), reason="需要 Windows DPAPI")
def test_tampered_ciphertext_returns_none():
    stored = secure_store.protect("secret")
    broken = stored[:-8] + base64.b64encode(b"garbage").decode("ascii")
    assert secure_store.unprotect(broken) is None


@pytest.mark.skipif(not secure_store.is_available(), reason="需要 Windows DPAPI")
def test_empty_password_stays_empty():
    assert secure_store.protect("") == ""