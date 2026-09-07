import pytest

from polymarket_agent.config import assert_no_trading_secrets, load_settings


def test_settings_are_read_only():
    assert load_settings().app.read_only is True


def test_scanner_refuses_private_key(monkeypatch):
    monkeypatch.setenv("POLYMARKET_PRIVATE_KEY", "never-use-this")
    with pytest.raises(RuntimeError, match="Refusing to start read-only scanner"):
        assert_no_trading_secrets()

