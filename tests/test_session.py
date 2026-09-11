from __future__ import annotations

import asyncio
import hashlib
import json

import pytest

from polymarket_agent.session import SessionOptions, run_capture_session


def _sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.asyncio
async def test_offline_capture_session_is_complete_and_self_auditing(tmp_path):
    output = tmp_path / "capture-001"
    result = await run_capture_session(SessionOptions(output, cycles=2, offline=True))

    assert result["outcome"] == "completed"
    assert result["healthy"] is True
    assert result["provenance_status"] in {
        "clean_git", "uncommitted_changes", "unavailable"
    }
    assert result["run"]["attempted_cycles"] == 2
    assert result["run"]["successful_cycles"] == 2
    assert result["run"]["failed_cycles"] == 0
    assert result["run"]["market_failures"] == 0
    assert result["run"]["stop_reason"] == "max_cycles"
    assert result["audit_summary"]["counts"]["features"] == 2

    start = json.loads((output / "session-start.json").read_text())
    final = json.loads((output / "session.json").read_text())
    assert start["session_id"] == final["session_id"]
    assert start["read_only"] is True
    assert start["offline"] is True
    assert start["artifacts"]["database"] == "capture.sqlite3"
    assert set(start["runtime"]) == {
        "package_version", "python", "platform", "git_commit", "git_dirty", "git_status"
    }
    assert "T" in start["started_at"]
    assert (output / "AUDIT.md").read_text().startswith("# 唯讀掃描資料驗證報告")
    assert not (output / "capture.sqlite3-wal").exists()
    assert not (output / "capture.sqlite3-shm").exists()
    for name, digest in final["sha256"].items():
        assert _sha256(output / name) == digest


@pytest.mark.asyncio
async def test_capture_session_refuses_to_reuse_directory(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    sentinel = output / "keep.txt"
    sentinel.write_text("user data")

    with pytest.raises(FileExistsError):
        await run_capture_session(SessionOptions(output, cycles=1, offline=True))

    assert sentinel.read_text() == "user data"
    assert sorted(path.name for path in output.iterdir()) == ["keep.txt"]


@pytest.mark.asyncio
async def test_capture_session_requires_one_bounded_stop_condition(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        await run_capture_session(SessionOptions(tmp_path / "none", offline=True))
    with pytest.raises(ValueError, match="exactly one"):
        await run_capture_session(
            SessionOptions(tmp_path / "both", cycles=1, duration_seconds=1, offline=True)
        )


@pytest.mark.asyncio
async def test_duration_limited_session_records_stop_reason(tmp_path):
    result = await run_capture_session(
        SessionOptions(tmp_path / "duration", duration_seconds=0.001, offline=True)
    )
    assert result["outcome"] == "completed"
    assert result["run"]["attempted_cycles"] == 1
    assert result["run"]["stop_reason"] == "duration"


@pytest.mark.asyncio
async def test_cancelled_session_is_sealed(monkeypatch, tmp_path):
    async def cancel(*args, **kwargs):
        raise asyncio.CancelledError()

    monkeypatch.setattr("polymarket_agent.session.Scanner.run_forever", cancel)
    output = tmp_path / "cancelled"
    with pytest.raises(asyncio.CancelledError):
        await run_capture_session(SessionOptions(output, cycles=1, offline=True))

    final = json.loads((output / "session.json").read_text())
    assert final["outcome"] == "cancelled"
    assert final["healthy"] is False
    assert final["run"] is None
    assert final["audit_summary"]["counts"]["features"] == 0
