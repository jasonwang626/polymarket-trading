"""Bounded, read-only scanner sessions with immutable capture manifests."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import platform
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.data.crypto_feed import CryptoPriceFeed
from polymarket_agent.data.polymarket_us import PolymarketUSFeed
from polymarket_agent.data.public_http import PublicAPI
from polymarket_agent.data.storage import Storage
from polymarket_agent.discovery.polymarket_us import PolymarketUSDiscovery
from polymarket_agent.logging import configure_logging
from polymarket_agent.offline import OfflineDiscovery, OfflineMarketFeed, OfflinePriceFeed
from polymarket_agent.scanner.scanner import RunSummary, Scanner
from polymarket_agent.validation import audit_database, markdown_report

FORMAT_VERSION = 1


@dataclass(frozen=True)
class SessionOptions:
    output_dir: Path
    config: Path | None = None
    cycles: int | None = None
    duration_seconds: float | None = None
    max_markets: int | None = None
    offline: bool = False
    show_results: bool = True


def _json_bytes(value: Any) -> bytes:
    def encode(item: Any) -> str:
        if isinstance(item, datetime):
            return item.isoformat()
        if isinstance(item, Path):
            return str(item)
        raise TypeError(f"Cannot encode {type(item).__name__}")

    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, default=encode) + "\n").encode()


def _write_new(path: Path, value: Any) -> None:
    with path.open("xb") as handle:
        handle.write(_json_bytes(value))


def _write_text_new(path: Path, value: str) -> None:
    with path.open("x", encoding="utf-8") as handle:
        handle.write(value)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _package_version() -> str:
    try:
        return version("polymarket-trading")
    except PackageNotFoundError:
        return "unknown"


def source_revision(project_root: Path) -> dict[str, Any]:
    """Return the current Git revision without mutating the working tree."""
    try:
        revision = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        status = subprocess.run(
            ["git", "-C", str(project_root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        return {
            "git_commit": revision,
            "git_dirty": bool(status),
            "git_status": status.splitlines()[:20],
        }
    except (OSError, subprocess.SubprocessError):
        return {"git_commit": None, "git_dirty": None, "git_status": []}


def _flush_logs() -> None:
    for handler in logging.getLogger().handlers:
        handler.flush()


def _seal_database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        mode = connection.execute("PRAGMA journal_mode=DELETE").fetchone()[0]
        if str(mode).lower() != "delete":
            raise sqlite3.OperationalError(f"Unable to seal database; journal mode is {mode}")


def _validate_options(options: SessionOptions) -> None:
    if (options.cycles is None) == (options.duration_seconds is None):
        raise ValueError("Specify exactly one of cycles or duration_seconds")
    if options.cycles is not None and options.cycles < 1:
        raise ValueError("cycles must be positive")
    if options.duration_seconds is not None and options.duration_seconds <= 0:
        raise ValueError("duration_seconds must be positive")
    if options.max_markets is not None and not 1 <= options.max_markets <= 200:
        raise ValueError("max_markets must be 1..200")


def _summary_payload(summary: RunSummary | None) -> dict[str, Any] | None:
    return asdict(summary) if summary is not None else None


async def run_capture_session(options: SessionOptions) -> dict[str, Any]:
    """Run a bounded capture. An existing output directory is never reused."""
    _validate_options(options)
    assert_no_trading_secrets()
    settings = load_settings(options.config)
    revision = source_revision(settings.project_root)
    output_dir = options.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=False)
    database_path = output_dir / "capture.sqlite3"
    log_path = output_dir / "scanner.jsonl"
    settings.storage.sqlite_path = database_path
    settings.app.log_path = log_path
    if options.max_markets is not None:
        settings.scanner.max_watchlist_size = options.max_markets

    config_snapshot = settings.model_dump(mode="json")
    config_hash = hashlib.sha256(_json_bytes(config_snapshot)).hexdigest()
    session_id = str(uuid.uuid4())
    started_at = datetime.now(UTC)
    start_manifest = {
        "format_version": FORMAT_VERSION,
        "session_id": session_id,
        "started_at": started_at,
        "read_only": True,
        "offline": options.offline,
        "requested": {
            "cycles": options.cycles,
            "duration_seconds": options.duration_seconds,
            "max_markets": options.max_markets,
        },
        "artifacts": {"database": database_path.name, "structured_log": log_path.name},
        "runtime": {
            "package_version": _package_version(),
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            **revision,
        },
        "config_sha256": config_hash,
        "config": config_snapshot,
    }
    _write_new(output_dir / "session-start.json", start_manifest)
    configure_logging(settings.app.log_level, log_path)
    storage = Storage(database_path)
    storage.initialize()

    public_api: PublicAPI | None = None
    if options.offline:
        fixture_dir = settings.project_root / "tests/fixtures"
        discovery = OfflineDiscovery(fixture_dir)
        market_feed = OfflineMarketFeed(fixture_dir)
        price_feed = OfflinePriceFeed()
    else:
        public_api = PublicAPI(settings)
        discovery = PolymarketUSDiscovery(settings, public_api)
        market_feed = PolymarketUSFeed(settings, public_api)
        price_feed = CryptoPriceFeed(settings)
    scanner = Scanner(settings, storage, discovery, market_feed, price_feed)
    summary: RunSummary | None = None
    outcome = "failed"
    failure_type: str | None = None
    cancelled: asyncio.CancelledError | None = None
    try:
        summary = await scanner.run_forever(
            max_cycles=options.cycles,
            max_duration_seconds=options.duration_seconds,
            show_results=options.show_results,
        )
        outcome = "completed"
        if summary.failed_cycles or summary.market_failures:
            outcome = "completed_with_errors"
    except asyncio.CancelledError as exc:
        outcome = "cancelled"
        cancelled = exc
    except Exception as exc:
        failure_type = type(exc).__name__
        raise
    finally:
        await scanner.close()
        if public_api is not None:
            await public_api.close()
        _seal_database(database_path)
        audit: dict[str, Any] | None = None
        audit_error: str | None = None
        try:
            audit = audit_database(database_path)
            _write_new(output_dir / "audit.json", audit)
            _write_text_new(output_dir / "AUDIT.md", markdown_report(audit))
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            audit_error = type(exc).__name__
        _flush_logs()
        artifact_names = ["session-start.json", "capture.sqlite3", "scanner.jsonl"]
        artifact_names += [name for name in ("audit.json", "AUDIT.md")
                           if (output_dir / name).is_file()]
        healthy = bool(
            outcome == "completed"
            and audit
            and audit["integrity"] == ["ok"]
            and audit["foreign_key_errors"] == 0
            and summary
            and summary.successful_cycles > 0
        )
        if revision["git_commit"] and revision["git_dirty"] is False:
            provenance_status = "clean_git"
        elif revision["git_dirty"] is True:
            provenance_status = "uncommitted_changes"
        else:
            provenance_status = "unavailable"
        final_manifest = {
            "format_version": FORMAT_VERSION,
            "session_id": session_id,
            "started_at": started_at,
            "ended_at": datetime.now(UTC),
            "outcome": outcome,
            "healthy": healthy,
            "provenance_status": provenance_status,
            "failure_type": failure_type,
            "audit_error": audit_error,
            "run": _summary_payload(summary),
            "audit_summary": ({
                "integrity": audit["integrity"],
                "foreign_key_errors": audit["foreign_key_errors"],
                "counts": audit["counts"],
                "window_start_utc": audit["window_start_utc"],
                "window_end_utc": audit["window_end_utc"],
                "statuses": audit["statuses"],
                "quality_flags_overlapping": audit["quality_flags_overlapping"],
                "decision_reason_categories_overlapping": (
                    audit["decision_reason_categories_overlapping"]
                ),
            } if audit else None),
            "sha256": {name: _sha256(output_dir / name) for name in artifact_names},
        }
        _write_new(output_dir / "session.json", final_manifest)
    if cancelled is not None:
        raise cancelled
    return final_manifest


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="建立不可覆寫的 Polymarket US 唯讀資料工作階段")
    parser.add_argument("output_dir", type=Path, help="必須尚不存在的工作階段目錄")
    parser.add_argument("--config", type=Path, help="settings.yaml 路徑")
    limit = parser.add_mutually_exclusive_group(required=True)
    limit.add_argument("--cycles", type=int, help="完成指定輪數後封存")
    limit.add_argument("--duration-minutes", type=float, help="執行指定分鐘後封存")
    parser.add_argument("--max-markets", type=int, help="限制監控合約數")
    parser.add_argument("--offline", action="store_true", help="使用 fixtures，不呼叫網路")
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    options = SessionOptions(
        output_dir=args.output_dir,
        config=args.config,
        cycles=args.cycles,
        duration_seconds=(args.duration_minutes * 60 if args.duration_minutes is not None else None),
        max_markets=args.max_markets,
        offline=args.offline,
    )
    try:
        result = asyncio.run(run_capture_session(options))
    except KeyboardInterrupt:
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
