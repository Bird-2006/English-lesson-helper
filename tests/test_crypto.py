import base64

from core.crypto import generate_cipher_text, to_hex_byte_array


def _decode(cipher: str) -> str:
    return base64.b64decode(cipher.encode("utf-8")).decode("utf-8")


def test_cipher_structure_matches_server_contract():
    password = "p@ssw0rd"
    cipher, timestamp = generate_cipher_text(password, now_ms=1700000000123)

    prefix, hex_password = _decode(cipher).split("*")
    assert prefix == timestamp
    assert hex_password == password.encode("utf-8").hex()
    assert hex_password == to_hex_byte_array(password.encode("utf-8"))


def test_timestamp_keeps_checksum_in_last_two_digits():
    password = "abc"
    now_ms = 1700000000123
    _, timestamp = generate_cipher_text(password, now_ms=now_ms)
    t1 = int(timestamp)

    checksum = (now_ms >> 16) & 0xFF
    for byte in password.encode("utf-8"):
        checksum ^= byte

    assert t1 % 100 == checksum % 100
    # T1 只替换了末两位，其余部分应来自 T0
    assert abs(t1 - now_ms) < 100


def test_unicode_password_roundtrip():
    password = "口令123"
    cipher, _ = generate_cipher_text(password, now_ms=1700000000000)
    _, hex_password = _decode(cipher).split("*")
    assert bytes.fromhex(hex_password).decode("utf-8") == password


def test_same_input_is_deterministic_for_fixed_timestamp():
    assert generate_cipher_text("abc", now_ms=1_000_000_000_000) == generate_cipher_text(
        "abc", now_ms=1_000_000_000_000
    )