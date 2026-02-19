"""Tests for memory retrieval telemetry and feedback hooks."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from core.memory import MemoryManager, MemoryRecord


def _row(memory_id: int = 1, *, summary: str = "remembered") -> MemoryRecord:
    now = datetime.now(timezone.utc)
    return MemoryRecord(
        memory_id=memory_id,
        owner_user_id="u-1",
        tier="short",
        memory_type="turn",
        domain="engineering",
        topic="memory",
        item="item",
        summary=summary,
        content="content",
        importance=0.5,
        confidence=0.8,
        pinned=False,
        is_shared_skill=False,
        skill_name=None,
        access_count=1,
        score=0.42,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.asyncio
async def test_search_memories_with_event_logs_retrieval(monkeypatch):
    mgr = MemoryManager({"enabled": True})

    async def fake_embed(_text: str):
        return None

    def fake_search_text(_user_id: str, _query: str, _limit: int):
        return [_row(memory_id=9)]

    captured = {}

    def fake_log_event(user_id, session_id, channel, query, result_count, top_score, latency_ms, used_vector, fallback):
        captured["args"] = (user_id, session_id, channel, query, result_count, top_score, latency_ms, used_vector, fallback)
        return 77

    monkeypatch.setattr(mgr, "_embed", fake_embed)
    monkeypatch.setattr(mgr, "_search_text_sync", fake_search_text)
    monkeypatch.setattr(mgr, "_log_retrieval_event_sync", fake_log_event)

    rows, retrieval_id = await mgr.search_memories_with_event(
        user_id="u-1",
        query="deploy",
        session_id="s-1",
        channel="telegram",
        limit=5,
    )

    assert retrieval_id == 77
    assert len(rows) == 1
    args = captured["args"]
    assert args[0] == "u-1"
    assert args[1] == "s-1"
    assert args[2] == "telegram"
    assert args[3] == "deploy"
    assert args[4] == 1
    assert args[5] == pytest.approx(0.42)
    assert args[7] is False


@pytest.mark.asyncio
async def test_build_memory_context_marks_injection(monkeypatch):
    mgr = MemoryManager({"enabled": True})
    marks = []

    async def fake_search_with_event(**_kwargs):
        return ([_row(memory_id=2, summary="deploy steps")], 55)

    def fake_mark(retrieval_id: int, user_id: str, injected_count: int):
        marks.append((retrieval_id, user_id, injected_count))
        return True

    monkeypatch.setattr(mgr, "search_memories_with_event", fake_search_with_event)
    monkeypatch.setattr(mgr, "_mark_retrieval_context_injected_sync", fake_mark)

    out = await mgr.build_memory_context(user_id="u-2", query="deploy", session_id="s-2", channel="telegram")
    assert "[MEMORY CONTEXT]" in out
    assert marks == [(55, "u-2", 1)]

