from datetime import UTC, datetime

from polymarket_agent.discovery.polymarket import (
    contains_keyword,
    infer_strike,
    infer_underlying,
    parse_market,
)


def test_parse_gamma_market():
    payload = {
        "id": "123",
        "conditionId": "0xabc",
        "slug": "btc-above-100k",
        "question": "Will Bitcoin be above $100,000 tomorrow?",
        "outcomes": '["Yes", "No"]',
        "clobTokenIds": '["yes-token", "no-token"]',
        "endDate": "2030-01-01T00:00:00Z",
        "liquidityNum": "1500.25",
        "volume24hr": 500,
    }
    market = parse_market(payload)
    assert market is not None
    assert market.yes_token_id == "yes-token"
    assert market.no_token_id == "no-token"
    assert market.underlying == "BTC-USD"
    assert market.strike == 100_000
    assert market.resolution_time.tzinfo is not None


def test_underlying_and_strike_inference():
    assert infer_underlying("ETH above 3.5k") == "ETH-USD"
    assert infer_strike("Will ETH be above $3.5k?", "ETH-USD") == 3500
    assert infer_underlying("Federal Reserve decision") is None
    assert infer_underlying("Will Elisabeth be the next prime minister?") is None
    assert contains_keyword("Elisabeth", "eth") is False
    assert contains_keyword("ETH above $3,000", "eth") is True


def test_parse_rejects_non_binary_market():
    assert (
        parse_market(
            {
                "id": "1",
                "conditionId": "x",
                "question": "Pick one",
                "outcomes": '["A", "B"]',
                "clobTokenIds": '["a", "b"]',
                "endDate": datetime.now(UTC).isoformat(),
            }
        )
        is None
    )
