from app.accounts.service import _digest


def test_raw_security_secret_is_not_stored_as_its_digest() -> None:
    raw = "a-token-that-is-never-persisted-directly"
    assert _digest(raw) != raw
