"""Resumable, read-only multi-session capture campaigns."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from polymarket_agent.config import assert_no_trading_secrets, load_settings
from polymarket_agent.session import (
    SessionOptions,
    _json_bytes,
    _write_new,
    run_capture_session,
    source_revision,
)

FORMAT_VERSION = 1
DEFAULT_INITIAL_FREE_BYTES = 40 * 1024**3
DEFAULT_RESERVE_FREE_BYTES = 5 * 1024**3


@dataclass(frozen=True)
class CampaignOptions:
    root_dir: Path
    days: int = 30
    session_duration_seconds: float = 24 * 60 * 60
    config: Path | None = None
    max_markets: int | None = None
    offline: bool = False
    resume: bool = False
    minimum_initial_free_bytes: int = DEFAULT_INITIAL_FREE_BYTES
    reserve_free_bytes: int = DEFAULT_RESERVE_FREE_BYTES


def _validate_options(options: CampaignOptions) -> None:
    if not 1 <= options.days <= 365:
        raise ValueError("days must be 1..365")
    if options.session_duration_seconds <= 0:
        raise ValueError("session_duration_seconds must be positive")
    if options.max_markets is not None and not 1 <= options.max_markets <= 200:
        raise ValueError("max_markets must be 1..200")
    if options.minimum_initial_free_bytes < 0 or options.reserve_free_bytes < 0:
        raise ValueError("disk thresholds cannot be negative")


def _event_hash(event_without_hash: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(event_without_hash)).hexdigest()


def _read_events(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    events: list[dict[str, Any]] = []
    previous_hash: str | None = None
    for sequence, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        event = json.loads(line)
        claimed_hash = event.pop("event_hash")
        if event.get("sequence") != sequence or event.get("previous_hash") != previous_hash:
            raise ValueError("campaign event chain sequence is invalid")
        if _event_hash(event) != claimed_hash:
            raise ValueError("campaign event chain hash is invalid")
        event["event_hash"] = claimed_hash
        events.append(event)
        previous_hash = claimed_hash
    return events


def _append_event(path: Path, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    events = _read_events(path)
    event = {
        "sequence": len(events) + 1,
        "previous_hash": events[-1]["event_hash"] if events else None,
        "recorded_at": datetime.now(UTC).isoformat(),
        "type": event_type,
        "payload": payload,
    }
    event = json.loads(_json_bytes(event))
    event["event_hash"] = _event_hash(event)
    encoded = json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(encoded)
        handle.flush()
        os.fsync(handle.fileno())
    return event


def _campaign_request(options: CampaignOptions) -> dict[str, Any]:
    return {
        "days": options.days,
        "session_duration_seconds": options.session_duration_seconds,
        "max_markets": options.max_markets,
        "offline": options.offline,
        "minimum_initial_free_bytes": options.minimum_initial_free_bytes,
        "reserve_free_bytes": options.reserve_free_bytes,
    }


def _require_same_request(start: dict[str, Any], options: CampaignOptions) -> None:
    if start["requested"] != _campaign_request(options):
        raise ValueError("resume options do not match campaign-start.json")


def _runtime_state(options: CampaignOptions) -> tuple[dict[str, Any], str]:
    settings = load_settings(options.config)
    revision = source_revision(settings.project_root)
    config_hash = hashlib.sha256(_json_bytes(settings.model_dump(mode="json"))).hexdigest()
    return revision, config_hash


def _require_runtime(start: dict[str, Any] | None, options: CampaignOptions) -> tuple[dict[str, Any], str]:
    revision, config_hash = _runtime_state(options)
    if not options.offline and (
        not revision["git_commit"] or revision["git_dirty"] is not False
    ):
        raise ValueError("live campaign requires a clean Git commit")
    if start is not None and start["runtime"]["git_commit"] != revision["git_commit"]:
        raise ValueError("resume requires the same Git commit as campaign start")
    if start is not None and start["config_sha256"] != config_hash:
        raise ValueError("campaign configuration changed after campaign start")
    return revision, config_hash


def _existing_ancestor(path: Path) -> Path:
    candidate = path.resolve()
    while not candidate.exists():
        if candidate == candidate.parent:
            raise FileNotFoundError(f"no existing parent for {path}")
        candidate = candidate.parent
    return candidate


def _completed_sessions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event["payload"] for event in events
        if event["type"] == "session_completed"
        and event["payload"]["outcome"] in {"completed", "completed_with_errors"}
    ]


def _next_attempt(root: Path, day: int) -> tuple[int, Path]:
    attempt = 1
    while True:
        path = root / f"day-{day:03d}-attempt-{attempt:03d}"
        if not path.exists():
            return attempt, path
        attempt += 1


def _aggregate(start: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    sessions = _completed_sessions(events)
    return {
        "format_version": FORMAT_VERSION,
        "campaign_id": start["campaign_id"],
        "started_at": start["started_at"],
        "ended_at": datetime.now(UTC),
        "outcome": "completed",
        "healthy": len(sessions) == start["requested"]["days"]
        and all(item["healthy"] for item in sessions),
        "read_only": True,
        "completed_sessions": len(sessions),
        "requested_sessions": start["requested"]["days"],
        "attempted_cycles": sum((item.get("run") or {}).get("attempted_cycles", 0) for item in sessions),
        "successful_cycles": sum((item.get("run") or {}).get("successful_cycles", 0) for item in sessions),
        "market_failures": sum((item.get("run") or {}).get("market_failures", 0) for item in sessions),
        "session_directories": [item["directory"] for item in sessions],
        "event_chain_head": events[-1]["event_hash"] if events else None,
    }


async def run_campaign(options: CampaignOptions) -> dict[str, Any]:
    """Run or resume a campaign without reusing or deleting a session directory."""
    _validate_options(options)
    assert_no_trading_secrets()
    root = options.root_dir.resolve()
    start_path = root / "campaign-start.json"
    events_path = root / "events.jsonl"
    final_path = root / "campaign.json"

    if options.resume:
        if not start_path.is_file():
            raise FileNotFoundError("campaign-start.json not found for resume")
        if final_path.exists():
            return json.loads(final_path.read_text(encoding="utf-8"))
        start = json.loads(start_path.read_text(encoding="utf-8"))
        _require_same_request(start, options)
        _require_runtime(start, options)
        events = _read_events(events_path)
        _append_event(events_path, "campaign_resumed", {"completed_sessions": len(_completed_sessions(events))})
    else:
        revision, config_hash = _require_runtime(None, options)
        free_bytes = shutil.disk_usage(_existing_ancestor(root.parent)).free
        if free_bytes < options.minimum_initial_free_bytes:
            raise OSError(
                f"insufficient free disk space: {free_bytes} < {options.minimum_initial_free_bytes}"
            )
        root.mkdir(parents=True, exist_ok=False)
        start = {
            "format_version": FORMAT_VERSION,
            "campaign_id": hashlib.sha256(os.urandom(32)).hexdigest()[:32],
            "started_at": datetime.now(UTC),
            "read_only": True,
            "requested": _campaign_request(options),
            "runtime": revision,
            "config_sha256": config_hash,
            "initial_free_bytes": free_bytes,
        }
        _write_new(start_path, start)
        _append_event(events_path, "campaign_started", {"initial_free_bytes": free_bytes})

    while True:
        events = _read_events(events_path)
        completed = _completed_sessions(events)
        if len(completed) >= options.days:
            final = json.loads(_json_bytes(_aggregate(start, events)))
            _write_new(final_path, final)
            return final
        _require_runtime(start, options)
        free_bytes = shutil.disk_usage(root).free
        if free_bytes < options.reserve_free_bytes:
            _append_event(events_path, "campaign_paused_low_disk", {"free_bytes": free_bytes})
            return {
                "outcome": "paused_low_disk",
                "healthy": False,
                "completed_sessions": len(completed),
                "requested_sessions": options.days,
            }
        day = len(completed) + 1
        attempt, session_dir = _next_attempt(root, day)
        _append_event(events_path, "session_started", {
            "day": day, "attempt": attempt, "directory": session_dir.name, "free_bytes": free_bytes
        })
        try:
            result = await run_capture_session(SessionOptions(
                output_dir=session_dir,
                config=options.config,
                duration_seconds=options.session_duration_seconds,
                max_markets=options.max_markets,
                offline=options.offline,
                show_results=False,
            ))
        except asyncio.CancelledError:
            _append_event(events_path, "session_cancelled", {
                "day": day, "attempt": attempt, "directory": session_dir.name
            })
            raise
        except Exception as exc:
            _append_event(events_path, "session_failed", {
                "day": day,
                "attempt": attempt,
                "directory": session_dir.name,
                "failure_type": type(exc).__name__,
            })
            raise
        _append_event(events_path, "session_completed", {
            "day": day,
            "attempt": attempt,
            "directory": session_dir.name,
            "outcome": result["outcome"],
            "healthy": result["healthy"],
            "provenance_status": result["provenance_status"],
            "run": result["run"],
            "session_manifest_sha256": hashlib.sha256(
                (session_dir / "session.json").read_bytes()
            ).hexdigest(),
        })


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="執行可續跑的 Polymarket US 唯讀資料 campaign")
    parser.add_argument("root_dir", type=Path, help="campaign 根目錄")
    parser.add_argument("--days", type=int, default=30, help="每日分卷數，預設 30")
    parser.add_argument("--session-hours", type=float, default=24, help="每卷時數，預設 24")
    parser.add_argument("--config", type=Path, help="settings.yaml 路徑")
    parser.add_argument("--max-markets", type=int, help="限制監控合約數")
    parser.add_argument("--resume", action="store_true", help="從既有 campaign 安全續跑")
    parser.add_argument("--offline", action="store_true", help="使用 fixtures，不呼叫網路")
    parser.add_argument("--minimum-initial-free-gb", type=float, default=40)
    parser.add_argument("--reserve-free-gb", type=float, default=5)
    return parser.parse_args(argv)


def main() -> int:
    args = parse_args()
    options = CampaignOptions(
        root_dir=args.root_dir,
        days=args.days,
        session_duration_seconds=args.session_hours * 3600,
        config=args.config,
        max_markets=args.max_markets,
        offline=args.offline,
        resume=args.resume,
        minimum_initial_free_bytes=int(args.minimum_initial_free_gb * 1024**3),
        reserve_free_bytes=int(args.reserve_free_gb * 1024**3),
    )
    try:
        result = asyncio.run(run_campaign(options))
    except KeyboardInterrupt:
        return 130
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0 if result["healthy"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
