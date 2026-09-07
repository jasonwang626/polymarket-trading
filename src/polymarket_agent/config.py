from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field, model_validator


class AppConfig(BaseModel):
    environment: str = "development"
    read_only: bool = True
    log_level: str = "INFO"
    log_path: Path = Path("logs/scanner.jsonl")


class StorageConfig(BaseModel):
    sqlite_path: Path = Path("data/scanner.sqlite3")


class ApiConfig(BaseModel):
    gamma_base_url: str
    clob_base_url: str
    data_base_url: str
    coinbase_base_url: str
    kraken_base_url: str
    timeout_seconds: float = Field(default=12, gt=0, le=60)
    user_agent: str = "polymarket-readonly-agent/0.1"


class ScannerConfig(BaseModel):
    refresh_seconds: float = Field(default=10, ge=1)
    discovery_refresh_seconds: float = Field(default=300, ge=10)
    market_page_size: int = Field(default=100, ge=1, le=500)
    market_max_pages: int = Field(default=5, ge=1, le=50)
    max_watchlist_size: int = Field(default=20, ge=1, le=200)
    max_concurrency: int = Field(default=8, ge=1, le=50)
    recent_trades_limit: int = Field(default=500, ge=1, le=10000)
    external_price_stale_seconds: int = Field(default=30, ge=1)
    history_lookback_minutes: int = Field(default=20, ge=15)


class FilterConfig(BaseModel):
    crypto_keywords: list[str]
    min_liquidity_usd: float = Field(default=1000, ge=0)
    min_volume_24h_usd: float = Field(default=100, ge=0)
    min_hours_to_expiry: float = Field(default=0.05, ge=0)
    max_hours_to_expiry: float = Field(default=168, gt=0)
    max_spread: float = Field(default=0.08, gt=0, le=1)


class SignalConfig(BaseModel):
    min_orderbook_imbalance: float = Field(default=0.60, ge=0.5, le=1)
    max_orderbook_imbalance: float = Field(default=0.40, ge=0, le=0.5)
    min_momentum_5m: float = Field(default=0.005, ge=0)
    min_external_return_5m: float = Field(default=0.001, ge=0)
    min_signal_score: int = Field(default=2, ge=1, le=3)


class Settings(BaseModel):
    app: AppConfig
    storage: StorageConfig
    api: ApiConfig
    scanner: ScannerConfig
    filters: FilterConfig
    signals: SignalConfig
    project_root: Path = Field(exclude=True)

    @model_validator(mode="after")
    def enforce_read_only(self) -> Settings:
        if not self.app.read_only:
            raise ValueError("Phase 0 / Sprint 1 requires app.read_only=true")
        if self.filters.min_hours_to_expiry >= self.filters.max_hours_to_expiry:
            raise ValueError("min_hours_to_expiry must be less than max_hours_to_expiry")
        return self

    def resolve_paths(self) -> Settings:
        if not self.app.log_path.is_absolute():
            self.app.log_path = self.project_root / self.app.log_path
        if not self.storage.sqlite_path.is_absolute():
            self.storage.sqlite_path = self.project_root / self.storage.sqlite_path
        return self


FORBIDDEN_SECRET_NAMES = {
    "POLYMARKET_PRIVATE_KEY",
    "POLYMARKET_API_KEY",
    "POLYMARKET_API_SECRET",
    "POLYMARKET_PASSPHRASE",
    "POLYMARKET_WALLET_ADDRESS",
}


def _apply_safe_env_overrides(raw: dict[str, Any]) -> None:
    if value := os.getenv("POLY_AGENT_ENV"):
        raw.setdefault("app", {})["environment"] = value
    if value := os.getenv("POLY_AGENT_LOG_LEVEL"):
        raw.setdefault("app", {})["log_level"] = value


def load_settings(path: str | Path | None = None) -> Settings:
    config_path = Path(path) if path else Path(__file__).parents[2] / "config/settings.yaml"
    config_path = config_path.resolve()
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    _apply_safe_env_overrides(raw)
    settings = Settings(**raw, project_root=config_path.parent.parent)
    return settings.resolve_paths()


def assert_no_trading_secrets() -> None:
    present = sorted(name for name in FORBIDDEN_SECRET_NAMES if os.getenv(name))
    if present:
        names = ", ".join(present)
        raise RuntimeError(
            f"Refusing to start read-only scanner while trading credentials are present: {names}"
        )
