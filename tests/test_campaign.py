from __future__ import annotations

import asyncio
import json

import pytest

from polymarket_agent.campaign import CampaignOptions, _read_events, run_campaign


def _options(root, **overrides):
    values = {
        "root_dir": root,
        "days": 2,
        "session_duration_seconds": 0.001,
        "offline": True,
        "minimum_initial_free_bytes": 0,
        "reserve_free_bytes": 0,
    }
    values.update(overrides)
    return CampaignOptions(**values)


@pytest.mark.asyncio
async def test_offline_campaign_rolls_sessions_and_seals_manifest(capsys, tmp_path):
    root = tmp_path / "campaign"
    result = await run_campaign(_options(root))

    assert result["outcome"] == "completed"
    assert result["healthy"] is True
    assert result["completed_sessions"] == 2
    assert result["requested_sessions"] == 2
    assert result["attempted_cycles"] == 2
    assert result["successful_cycles"] == 2
    assert result["session_directories"] == [
        "day-001-attempt-001", "day-002-attempt-001"
    ]
    assert (root / "campaign-start.json").is_file()
    assert (root / "campaign.json").is_file()
    assert capsys.readouterr().out == ""
    for directory in result["session_directories"]:
        assert (root / directory / "session.json").is_file()

    events = _read_events(root / "events.jsonl")
    assert [event["type"] for event in events] == [
        "campaign_started", "session_started", "session_completed",
        "session_started", "session_completed",
    ]
    assert result["event_chain_head"] == events[-1]["event_hash"]
    assert all(
        event["payload"].get("session_manifest_sha256")
        for event in events if event["type"] == "session_completed"
    )


@pytest.mark.asyncio
async def test_completed_campaign_resume_is_idempotent(tmp_path):
    root = tmp_path / "campaign"
    original = await run_campaign(_options(root, days=1))
    resumed = await run_campaign(_options(root, days=1, resume=True))

    assert resumed == original
    assert len(list(root.glob("day-*"))) == 1


@pytest.mark.asyncio
async def test_resume_never_reuses_cancelled_session_directory(monkeypatch, tmp_path):
    root = tmp_path / "campaign"
    real_runner = __import__(
        "polymarket_agent.campaign", fromlist=["run_capture_session"]
    ).run_capture_session

    async def cancelled_runner(options):
        options.output_dir.mkdir()
        (options.output_dir / "partial.txt").write_text("keep", encoding="utf-8")
        raise asyncio.CancelledError()

    monkeypatch.setattr("polymarket_agent.campaign.run_capture_session", cancelled_runner)
    with pytest.raises(asyncio.CancelledError):
        await run_campaign(_options(root, days=1))

    monkeypatch.setattr("polymarket_agent.campaign.run_capture_session", real_runner)
    result = await run_campaign(_options(root, days=1, resume=True))

    assert result["healthy"] is True
    assert (root / "day-001-attempt-001" / "partial.txt").read_text() == "keep"
    assert (root / "day-001-attempt-002" / "session.json").is_file()
    types = [event["type"] for event in _read_events(root / "events.jsonl")]
    assert types == [
        "campaign_started", "session_started", "session_cancelled",
        "campaign_resumed", "session_started", "session_completed",
    ]


@pytest.mark.asyncio
async def test_resume_rejects_changed_options_and_tampered_event_chain(tmp_path):
    root = tmp_path / "campaign"
    await run_campaign(_options(root, days=1))
    (root / "campaign.json").unlink()

    with pytest.raises(ValueError, match="do not match"):
        await run_campaign(_options(root, days=2, resume=True))

    lines = (root / "events.jsonl").read_text(encoding="utf-8").splitlines()
    event = json.loads(lines[0])
    event["payload"]["initial_free_bytes"] += 1
    lines[0] = json.dumps(event)
    (root / "events.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash is invalid"):
        await run_campaign(_options(root, days=1, resume=True))


@pytest.mark.asyncio
async def test_campaign_pauses_before_session_when_reserve_is_unavailable(
    monkeypatch, tmp_path
):
    root = tmp_path / "campaign"
    usage = type("Usage", (), {"free": 100})()
    monkeypatch.setattr("polymarket_agent.campaign.shutil.disk_usage", lambda path: usage)

    result = await run_campaign(_options(
        root, days=1, minimum_initial_free_bytes=50, reserve_free_bytes=101
    ))

    assert result == {
        "outcome": "paused_low_disk",
        "healthy": False,
        "completed_sessions": 0,
        "requested_sessions": 1,
    }
    assert not list(root.glob("day-*"))
    assert _read_events(root / "events.jsonl")[-1]["type"] == "campaign_paused_low_disk"


@pytest.mark.asyncio
async def test_live_campaign_requires_clean_git_before_creating_root(monkeypatch, tmp_path):
    root = tmp_path / "campaign"
    monkeypatch.setattr(
        "polymarket_agent.campaign.source_revision",
        lambda project_root: {"git_commit": "abc", "git_dirty": True},
    )

    with pytest.raises(ValueError, match="clean Git commit"):
        await run_campaign(_options(root, days=1, offline=False))

    assert not root.exists()
