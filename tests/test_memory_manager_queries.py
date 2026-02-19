"""Query-scope regression tests for MemoryManager SQL parameters."""

from __future__ import annotations

from typing import Any, List, Optional, Sequence, Tuple

from core.memory import MemoryManager


class _FakeCursor:
    def __init__(
        self,
        *,
        fetchall_batches: Optional[Sequence[Sequence[Tuple[Any, ...]]]] = None,
        fetchone_value: Optional[Tuple[Any, ...]] = None,
    ) -> None:
        self.executions: List[Tuple[str, Optional[Tuple[Any, ...]]]] = []
        self._fetchall_batches = [list(batch) for batch in (fetchall_batches or [[]])]
        self._fetchone_value = fetchone_value

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params=None) -> None:
        self.executions.append((sql, params))

    def fetchall(self):
        if not self._fetchall_batches:
            return []
        return self._fetchall_batches.pop(0)

    def fetchone(self):
        return self._fetchone_value


class _FakeConn:
    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.commit_count = 0

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self):
        return self._cursor

    def commit(self) -> None:
        self.commit_count += 1


def _build_manager(fake_conn: _FakeConn) -> MemoryManager:
    mgr = MemoryManager({"enabled": False})
    mgr._conn = lambda: fake_conn  # type: ignore[method-assign]
    return mgr


def test_search_text_includes_system_owner_scope():
    cur = _FakeCursor(fetchall_batches=[[], []])
    mgr = _build_manager(_FakeConn(cur))

    assert mgr._search_text_sync("u-1", "deploy", 5) == []

    assert len(cur.executions) == 2
    first_params = cur.executions[0][1]
    second_params = cur.executions[1][1]
    assert first_params == ("deploy", "u-1", mgr.SYSTEM_OWNER, "deploy", 5)
    assert second_params == ("u-1", mgr.SYSTEM_OWNER, 5)


def test_list_memories_includes_system_owner_scope():
    cur = _FakeCursor(fetchall_batches=[[]])
    mgr = _build_manager(_FakeConn(cur))

    assert mgr._list_memories_sync("u-1", None, 20) == []
    assert len(cur.executions) == 1
    assert cur.executions[0][1] == ("u-1", mgr.SYSTEM_OWNER, 20)


def test_get_memory_includes_system_owner_scope():
    cur = _FakeCursor(fetchone_value=None)
    mgr = _build_manager(_FakeConn(cur))

    assert mgr._get_memory_sync("u-1", 42) is None
    assert len(cur.executions) == 1
    assert cur.executions[0][1] == (42, "u-1", mgr.SYSTEM_OWNER)

