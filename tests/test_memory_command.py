"""Tests for /memory command behavior."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from core.router import Router


@dataclass
class _FakeRow:
    memory_id: int
    tier: str = "short"
    domain: str = "engineering"
    topic: str = "memory"
    summary: str = "remembered summary"
    pinned: bool = False
    is_shared_skill: bool = False
    skill_name: Optional[str] = None
    score: float = 0.9
    memory_type: str = "note"
    item: str = "item"
    content: str = "full content"


class FakeMemoryManager:
    enabled = True

    async def health_stats(self):
        return {"total_items": 3, "shared_skills": 1, "vector_supported": True}

    async def user_stats(self, *, user_id: str):
        return {"user_items": 3, "vector_supported": True}

    async def list_memories(self, *, user_id: str, tier: Optional[str] = None, limit: int = 20):
        return [_FakeRow(memory_id=1, tier=tier or "short")]

    async def search_memories(self, *, user_id: str, query: str, limit: int = 6, min_score: float = 0.2):
        return [_FakeRow(memory_id=2, tier="mid", summary=f"match:{query}")]

    async def search_memories_with_event(
        self,
        *,
        user_id: str,
        query: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 6,
        min_score: float = 0.2,
    ):
        return ([_FakeRow(memory_id=2, tier="mid", summary=f"match:{query}")], 101)

    async def get_memory(self, *, user_id: str, memory_id: int):
        if memory_id == 9:
            return None
        return _FakeRow(memory_id=memory_id)

    async def add_note(self, *, user_id: str, scope_id: str, session_id: Optional[str], channel: str, text: str):
        return 7

    async def set_pinned(self, *, user_id: str, memory_id: int, pinned: bool):
        return True

    async def forget_memory(self, *, user_id: str, memory_id: int):
        return True

    async def record_retrieval_feedback(self, *, user_id: str, retrieval_id: int, feedback: str, note: Optional[str] = None):
        return retrieval_id == 101 and feedback in {"good", "bad"}

    async def retrieval_stats(self, *, user_id: str, days: int = 7):
        return {
            "total_queries": 10,
            "hit_rate": 0.7,
            "context_inject_rate": 0.6,
            "avg_result_count": 3.2,
            "avg_latency_ms": 12.5,
            "vector_usage_rate": 0.8,
            "feedback_coverage": 0.5,
            "positive_feedback_rate": 0.75,
        }

    async def recent_retrieval_events(self, *, user_id: str, limit: int = 5):
        return [
            type("Ev", (), {"retrieval_id": 101, "result_count": 2, "context_injected": True, "feedback": "good", "query": "deploy"})
        ]

class TestMemoryCommand:
    @pytest.mark.asyncio
    async def test_memory_disabled_without_manager(self, router, make_message, fake_channel):
        await router.handle_message(make_message(text="/memory"))
        text = fake_channel.last_sent_text() or ""
        assert "未启用" in text

    @pytest.mark.asyncio
    async def test_memory_list(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel, make_message
    ):
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
            memory_manager=FakeMemoryManager(),
        )
        await r.handle_message(make_message(text="/memory list"))
        assert "记忆列表" in (fake_channel.last_sent_text() or "")

    @pytest.mark.asyncio
    async def test_memory_status_shows_user_scoped_count(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel, make_message
    ):
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
            memory_manager=FakeMemoryManager(),
        )
        await r.handle_message(make_message(text="/memory"))
        text = fake_channel.last_sent_text() or ""
        assert "my_items" in text
        assert "shared_skills" not in text

    @pytest.mark.asyncio
    async def test_memory_find_and_share_disabled(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel, make_message
    ):
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
            memory_manager=FakeMemoryManager(),
        )
        await r.handle_message(make_message(text="/memory find deploy"))
        text = fake_channel.last_sent_text() or ""
        assert "检索结果" in text
        assert "request_id" in text

        await r.handle_message(make_message(text="/memory share 1 my_skill"))
        assert "已禁用" in (fake_channel.last_sent_text() or "")

    @pytest.mark.asyncio
    async def test_memory_feedback_and_metrics(
        self, auth, session_manager, mock_agent, sample_config, billing, fake_channel, make_message
    ):
        r = Router(
            auth=auth,
            session_manager=session_manager,
            agents={"claude": mock_agent},
            channel=fake_channel,
            config=sample_config,
            billing=billing,
            memory_manager=FakeMemoryManager(),
        )
        await r.handle_message(make_message(text="/memory fb 101 good useful"))
        assert "已记录反馈" in (fake_channel.last_sent_text() or "")

        await r.handle_message(make_message(text="/memory metrics 7"))
        text = fake_channel.last_sent_text() or ""
        assert "记忆检索指标" in text
        assert "hit_rate" in text
        assert "req#101" in text
