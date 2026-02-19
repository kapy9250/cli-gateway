"""Integration tests for memory context injection and turn capture."""

from __future__ import annotations

import pytest

from core.router import Router


class FakeMemoryRuntime:
    enabled = True

    def __init__(self):
        self.build_calls = []
        self.captures = []

    async def build_memory_context(self, *, user_id: str, query: str, **kwargs):
        self.build_calls.append((user_id, query))
        return "[MEMORY CONTEXT]\n- remembered preference\n[END MEMORY CONTEXT]\n\n"

    async def capture_turn(
        self,
        *,
        user_id: str,
        scope_id: str,
        session_id: str,
        channel: str,
        user_text: str,
        assistant_text: str,
    ):
        self.captures.append((user_id, scope_id, session_id, channel, user_text, assistant_text))
        return 1


@pytest.mark.asyncio
async def test_router_injects_memory_and_captures_turn(
    auth,
    session_manager,
    mock_agent,
    sample_config,
    billing,
    fake_channel,
    make_message,
):
    mem = FakeMemoryRuntime()
    router = Router(
        auth=auth,
        session_manager=session_manager,
        agents={"claude": mock_agent},
        channel=fake_channel,
        config=sample_config,
        billing=billing,
        memory_manager=mem,
    )

    await router.handle_message(make_message(text="remember this preference"))
    assert len(mem.build_calls) == 1
    assert len(mem.captures) == 1
    assert len(mock_agent.messages_received) >= 1

    _, prompt = mock_agent.messages_received[0]
    assert "[MEMORY CONTEXT]" in prompt
