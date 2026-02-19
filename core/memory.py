"""PostgreSQL-backed memory system with tiers, tree taxonomy, and shared skills."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shlex
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import aiohttp

try:
    import psycopg
except Exception:  # pragma: no cover - handled at runtime when feature enabled
    psycopg = None

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _norm_text(text: str, max_chars: int = 2000) -> str:
    value = " ".join(str(text or "").split())
    if len(value) > max_chars:
        return value[:max_chars]
    return value


def _hash_text(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(str(part or "").encode("utf-8", errors="ignore"))
        h.update(b"\n")
    return h.hexdigest()


def _vector_literal(values: List[float]) -> str:
    return "[" + ",".join(f"{float(v):.8f}" for v in values) + "]"


def _safe_skill_slug(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", str(name or "").strip().lower()).strip("-")
    return slug or "shared-skill"


_SENSITIVE_PATTERNS = [
    re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(r"\b(?:password|passwd|token|secret)\s*[:=]\s*[^\s]{6,}", re.IGNORECASE),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b"),
]


@dataclass
class MemoryRecord:
    memory_id: int
    owner_user_id: str
    tier: str
    memory_type: str
    domain: str
    topic: str
    item: str
    summary: str
    content: str
    importance: float
    confidence: float
    pinned: bool
    is_shared_skill: bool
    skill_name: Optional[str]
    access_count: int
    score: float = 0.0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass
class RetrievalEvent:
    retrieval_id: int
    query: str
    result_count: int
    top_score: Optional[float]
    latency_ms: int
    context_injected: bool
    feedback: Optional[str]
    created_at: Optional[datetime] = None


class OpenAIEmbeddingClient:
    """Tiny OpenAI embedding client via aiohttp to avoid extra sdk dependency."""

    def __init__(self, cfg: Dict[str, Any]):
        self.endpoint = str(cfg.get("endpoint", "https://api.openai.com/v1/embeddings")).strip()
        self.model = str(cfg.get("model", "text-embedding-3-small")).strip()
        self.api_key_env = str(cfg.get("api_key_env", "OPENAI_API_KEY")).strip()
        self.timeout_seconds = float(cfg.get("timeout_seconds", 10.0))
        self.dimensions = int(cfg.get("dimensions", 1536))

    def is_configured(self) -> bool:
        key = os.environ.get(self.api_key_env, "").strip()
        return bool(self.endpoint and self.model and key)

    async def embed(self, text: str) -> Optional[List[float]]:
        api_key = os.environ.get(self.api_key_env, "").strip()
        if not api_key:
            return None

        payload: Dict[str, Any] = {"model": self.model, "input": str(text or "")}
        if self.dimensions > 0:
            payload["dimensions"] = self.dimensions
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.endpoint, json=payload, headers=headers) as resp:
                    if resp.status >= 400:
                        body = await resp.text()
                        logger.warning("Embedding request failed: status=%s body=%s", resp.status, body[:300])
                        return None
                    data = await resp.json()
            emb = (((data or {}).get("data") or [{}])[0] or {}).get("embedding")
            if not isinstance(emb, list) or not emb:
                return None
            return [float(v) for v in emb]
        except Exception as e:  # noqa: BLE001
            logger.warning("Embedding request error: %s", e)
            return None


class MemoryManager:
    """User-isolated memory manager (cross-session, no cross-user sharing)."""

    SYSTEM_OWNER = "__system__"

    def __init__(self, cfg: Dict[str, Any], *, runtime: Optional[Dict[str, Any]] = None):
        self.cfg = dict(cfg or {})
        self.runtime = dict(runtime or {})
        self.enabled = bool(self.cfg.get("enabled", False))
        self.dsn = str(self.cfg.get("dsn", "")).strip()

        tiers = self.cfg.get("tiers", {}) or {}
        self.promote_short_to_mid = int(tiers.get("promote_hits_short_to_mid", 3))
        self.promote_mid_to_long = int(tiers.get("promote_hits_mid_to_long", 8))

        retrieval = self.cfg.get("retrieval", {}) or {}
        self.default_top_k = int(retrieval.get("top_k", 6))
        self.default_context_char_limit = int(retrieval.get("context_char_limit", 1800))
        self.default_min_similarity = float(retrieval.get("min_similarity", 0.2))
        self.default_candidate_limit = int(retrieval.get("candidate_limit", 64))

        capture = self.cfg.get("capture", {}) or {}
        self.capture_enabled = bool(capture.get("enabled_auto", True))
        self.capture_max_chars = int(capture.get("max_content_chars", 2000))
        self.capture_assistant_max_chars = int(capture.get("assistant_max_chars", 2000))

        safety = self.cfg.get("safety", {}) or {}
        self.reject_sensitive = bool(safety.get("reject_sensitive", True))

        tree = self.cfg.get("tree", {}) or {}
        self.default_domain = str(tree.get("default_domain", "general")).strip() or "general"
        self.default_topic = str(tree.get("default_topic", "misc")).strip() or "misc"

        embedding_cfg = self.cfg.get("embedding", {}) or {}
        self.embedding_enabled = bool(embedding_cfg.get("enabled", True))
        self.embedder = OpenAIEmbeddingClient(embedding_cfg)
        self.embedding_dim = int(embedding_cfg.get("dimensions", 1536))

        skill_cfg = self.cfg.get("skill", {}) or {}
        self.shared_skill_enabled = False
        if bool(skill_cfg.get("shared_enabled", False)):
            logger.warning("memory.skill.shared_enabled is ignored: cross-user sharing is disabled")
        raw_export_dir = str(skill_cfg.get("export_dir", "./data/skills")).strip()
        self.skill_export_dir = Path(raw_export_dir)

        env_cfg = self.cfg.get("env_probe", {}) or {}
        self.env_probe_enabled = bool(env_cfg.get("enabled", False))
        self.env_probe_interval = max(60, int(env_cfg.get("interval_seconds", 3600)))
        self.env_probe_timeout = max(1, int(env_cfg.get("timeout_seconds", 5)))
        self.env_probe_max_chars = max(100, int(env_cfg.get("max_output_chars", 1000)))
        self.env_probe_commands = self._normalize_env_probe_commands(env_cfg.get("commands", []))

        self._started = False
        self._vector_supported = False
        self._use_vector_column = False
        self._stop_event = asyncio.Event()
        self._env_probe_task: Optional[asyncio.Task] = None
        self._last_probe_at: Optional[datetime] = None

    @staticmethod
    def _normalize_env_probe_commands(raw: Any) -> List[List[str]]:
        out: List[List[str]] = []
        if not isinstance(raw, list):
            return out
        for item in raw:
            if isinstance(item, list):
                cmd = [str(v).strip() for v in item if str(v).strip()]
                if cmd:
                    out.append(cmd)
            elif isinstance(item, str) and item.strip():
                out.append(shlex.split(item))
        return out

    @staticmethod
    def _contains_sensitive(text: str) -> bool:
        value = str(text or "")
        for pat in _SENSITIVE_PATTERNS:
            if pat.search(value):
                return True
        return False

    def _conn(self):
        if psycopg is None:
            raise RuntimeError("psycopg is not installed; install requirements to enable memory")
        if not self.dsn:
            raise ValueError("memory.dsn is required when memory.enabled=true")
        return psycopg.connect(self.dsn)

    async def start(self) -> None:
        if not self.enabled:
            return
        await asyncio.to_thread(self._init_schema)
        self._started = True
        if self.env_probe_enabled and self.env_probe_commands:
            self._env_probe_task = asyncio.create_task(self._env_probe_loop(), name="memory-env-probe")
        logger.info(
            "Memory manager started (vector=%s, embedder=%s, env_probe=%s)",
            self._vector_supported,
            self.embedder.is_configured() if self.embedding_enabled else False,
            bool(self._env_probe_task),
        )

    async def stop(self) -> None:
        self._stop_event.set()
        if self._env_probe_task:
            self._env_probe_task.cancel()
            try:
                await self._env_probe_task
            except asyncio.CancelledError:
                pass
        self._env_probe_task = None
        self._started = False

    def _init_schema(self) -> None:
        with self._conn() as conn:
            with conn.cursor() as cur:
                self._vector_supported = self._ensure_vector_extension(cur)

                if self._vector_supported:
                    embed_col = f"embedding vector({self.embedding_dim})"
                else:
                    embed_col = "embedding_text TEXT"

                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS memory_items (
                        id BIGSERIAL PRIMARY KEY,
                        owner_user_id TEXT NOT NULL,
                        source_scope_id TEXT,
                        session_id TEXT,
                        channel TEXT,
                        tier TEXT NOT NULL DEFAULT 'short',
                        memory_type TEXT NOT NULL DEFAULT 'turn',
                        domain TEXT NOT NULL DEFAULT 'general',
                        topic TEXT NOT NULL DEFAULT 'misc',
                        item TEXT NOT NULL DEFAULT 'item',
                        content TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        importance DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 0.5,
                        pinned BOOLEAN NOT NULL DEFAULT FALSE,
                        is_shared_skill BOOLEAN NOT NULL DEFAULT FALSE,
                        skill_name TEXT,
                        content_hash TEXT NOT NULL,
                        access_count INTEGER NOT NULL DEFAULT 0,
                        last_accessed_at TIMESTAMPTZ,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        is_deleted BOOLEAN NOT NULL DEFAULT FALSE,
                        {embed_col},
                        search_tsv tsvector GENERATED ALWAYS AS (
                            to_tsvector('simple', coalesce(summary, '') || ' ' || coalesce(content, ''))
                        ) STORED
                    )
                    """
                )
                # Stable conflict target for upserts (expression indexes are fragile for inference).
                cur.execute(
                    """
                    ALTER TABLE memory_items
                    ADD COLUMN IF NOT EXISTS skill_key TEXT GENERATED ALWAYS AS (coalesce(skill_name, '')) STORED
                    """
                )
                cur.execute(
                    """
                    DO $$
                    BEGIN
                        IF NOT EXISTS (
                            SELECT 1
                            FROM pg_constraint
                            WHERE conname = 'memory_unique_uniq'
                              AND conrelid = 'memory_items'::regclass
                        ) THEN
                            ALTER TABLE memory_items
                            ADD CONSTRAINT memory_unique_uniq
                            UNIQUE (owner_user_id, content_hash, memory_type, skill_key);
                        END IF;
                    END $$;
                    """
                )
                cur.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS memory_unique_idx
                    ON memory_items (owner_user_id, content_hash, memory_type, skill_key)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_owner_tier_idx
                    ON memory_items (owner_user_id, tier, updated_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_shared_idx
                    ON memory_items (is_shared_skill, skill_name)
                    """
                )
                # Ensure mixed old/new schemas keep a writable fallback column.
                cur.execute(
                    """
                    ALTER TABLE memory_items
                    ADD COLUMN IF NOT EXISTS embedding_text TEXT
                    """
                )
                if self._vector_supported:
                    try:
                        cur.execute(
                            f"""
                            ALTER TABLE memory_items
                            ADD COLUMN IF NOT EXISTS embedding vector({self.embedding_dim})
                            """
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Failed to add vector column, fallback to text embeddings: %s", e)
                        self._vector_supported = False
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_tree_idx
                    ON memory_items (owner_user_id, domain, topic, item)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_search_idx
                    ON memory_items USING GIN (search_tsv)
                    """
                )
                cur.execute(
                    """
                    CREATE TABLE IF NOT EXISTS memory_retrieval_events (
                        id BIGSERIAL PRIMARY KEY,
                        owner_user_id TEXT NOT NULL,
                        session_id TEXT,
                        channel TEXT,
                        query TEXT NOT NULL,
                        query_hash TEXT NOT NULL,
                        result_count INTEGER NOT NULL DEFAULT 0,
                        top_score DOUBLE PRECISION,
                        latency_ms INTEGER NOT NULL DEFAULT 0,
                        used_vector BOOLEAN NOT NULL DEFAULT FALSE,
                        fallback_to_text BOOLEAN NOT NULL DEFAULT FALSE,
                        context_injected BOOLEAN NOT NULL DEFAULT FALSE,
                        injected_count INTEGER NOT NULL DEFAULT 0,
                        feedback TEXT,
                        feedback_note TEXT,
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    )
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS session_id TEXT
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS channel TEXT
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS used_vector BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS fallback_to_text BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS context_injected BOOLEAN NOT NULL DEFAULT FALSE
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS injected_count INTEGER NOT NULL DEFAULT 0
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS feedback TEXT
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS feedback_note TEXT
                    """
                )
                cur.execute(
                    """
                    ALTER TABLE memory_retrieval_events
                    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_retrieval_owner_created_idx
                    ON memory_retrieval_events (owner_user_id, created_at DESC)
                    """
                )
                cur.execute(
                    """
                    CREATE INDEX IF NOT EXISTS memory_retrieval_query_hash_idx
                    ON memory_retrieval_events (owner_user_id, query_hash)
                    """
                )
                if self._vector_supported:
                    try:
                        cur.execute(
                            """
                            CREATE INDEX IF NOT EXISTS memory_embedding_idx
                            ON memory_items USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)
                            """
                        )
                    except Exception as e:  # noqa: BLE001
                        logger.warning("Failed to create pgvector ivfflat index: %s", e)
                cur.execute(
                    """
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_schema = current_schema()
                      AND table_name = 'memory_items'
                    """
                )
                cols = {str(row[0]) for row in cur.fetchall()}
                self._use_vector_column = bool(self._vector_supported and "embedding" in cols)
                conn.commit()

    def _ensure_vector_extension(self, cur) -> bool:
        """Detect vector availability without requiring CREATE privilege."""
        try:
            cur.execute("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')")
            installed = bool((cur.fetchone() or [False])[0])
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to probe pg_extension for vector: %s", e)
            installed = False

        if installed:
            return True

        try:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            return True
        except Exception as e:  # noqa: BLE001
            logger.warning("pgvector extension unavailable or insufficient privilege: %s", e)
            return False

    async def health_stats(self) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        stats = await asyncio.to_thread(self._health_stats_sync)
        stats["enabled"] = True
        stats["vector_supported"] = self._vector_supported
        stats["last_probe_at"] = self._last_probe_at.isoformat() if self._last_probe_at else None
        return stats

    async def user_stats(self, *, user_id: str) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        stats = await asyncio.to_thread(self._user_stats_sync, str(user_id))
        stats["enabled"] = True
        stats["vector_supported"] = self._vector_supported
        return stats

    def _health_stats_sync(self) -> Dict[str, Any]:
        out = {"total_items": 0, "shared_skills": 0}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM memory_items WHERE is_deleted = FALSE")
                out["total_items"] = int((cur.fetchone() or [0])[0])
                cur.execute(
                    "SELECT COUNT(*) FROM memory_items WHERE is_deleted = FALSE AND is_shared_skill = TRUE"
                )
                out["shared_skills"] = int((cur.fetchone() or [0])[0])
        return out

    def _user_stats_sync(self, user_id: str) -> Dict[str, Any]:
        out = {"user_items": 0}
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT COUNT(*) FROM memory_items
                    WHERE is_deleted = FALSE
                      AND owner_user_id = %s
                    """,
                    (user_id,),
                )
                out["user_items"] = int((cur.fetchone() or [0])[0])
        return out

    async def build_memory_context(
        self,
        *,
        user_id: str,
        query: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        top_k: Optional[int] = None,
        char_limit: Optional[int] = None,
        min_score: Optional[float] = None,
    ) -> str:
        if not self.enabled:
            return ""
        q = _norm_text(query, max_chars=800)
        if not q:
            return ""

        rows, retrieval_id = await self.search_memories_with_event(
            user_id=user_id,
            query=q,
            session_id=session_id,
            channel=channel,
            limit=top_k or self.default_top_k,
            min_score=min_score if min_score is not None else self.default_min_similarity,
        )
        if not rows:
            return ""

        budget = max(200, int(char_limit or self.default_context_char_limit))
        lines = ["[MEMORY CONTEXT]"]
        consumed = len(lines[0]) + 1
        for row in rows:
            label = f"- ({row.tier}|{row.domain}/{row.topic})"
            line = f"{label} {row.summary}"
            if consumed + len(line) + 1 > budget:
                break
            lines.append(line)
            consumed += len(line) + 1
        lines.append("[END MEMORY CONTEXT]")
        injected_count = max(0, len(lines) - 2)
        if retrieval_id is not None:
            try:
                await asyncio.to_thread(
                    self._mark_retrieval_context_injected_sync,
                    retrieval_id,
                    user_id,
                    injected_count,
                )
            except Exception as e:  # noqa: BLE001
                logger.warning("Failed to mark retrieval context injection: %s", e)
        return "\n".join(lines) + "\n\n"

    async def capture_turn(
        self,
        *,
        user_id: str,
        scope_id: str,
        session_id: str,
        channel: str,
        user_text: str,
        assistant_text: str,
    ) -> Optional[int]:
        if not self.enabled or not self.capture_enabled:
            return None

        u = _norm_text(user_text, max_chars=self.capture_max_chars)
        a = _norm_text(assistant_text, max_chars=self.capture_assistant_max_chars)
        if not u and not a:
            return None

        combined = f"User: {u}\nAssistant: {a}".strip()
        if self.reject_sensitive and self._contains_sensitive(combined):
            logger.info("Memory capture skipped due to sensitive pattern (user=%s)", user_id)
            return None

        memory_type, importance, confidence = self._classify_type(u, a)
        domain, topic, item = self._classify_tree(u, a)
        tier = self._initial_tier(memory_type, importance)
        summary = self._build_summary(u, a, domain, topic)
        embedding = await self._embed(summary + "\n" + combined)

        memory_id = await asyncio.to_thread(
            self._insert_memory_sync,
            user_id,
            scope_id,
            session_id,
            channel,
            tier,
            memory_type,
            domain,
            topic,
            item,
            combined,
            summary,
            importance,
            confidence,
            False,
            None,
            embedding,
        )
        return memory_id

    async def add_note(
        self,
        *,
        user_id: str,
        scope_id: str,
        session_id: Optional[str],
        channel: str,
        text: str,
    ) -> Optional[int]:
        if not self.enabled:
            return None
        note = _norm_text(text, max_chars=self.capture_max_chars)
        if not note:
            return None
        if self.reject_sensitive and self._contains_sensitive(note):
            return None

        domain, topic, item = self._classify_tree(note, "")
        summary = f"[manual] {note[:120]}"
        embedding = await self._embed(summary + "\n" + note)
        return await asyncio.to_thread(
            self._insert_memory_sync,
            user_id,
            scope_id,
            session_id,
            channel,
            "mid",
            "note",
            domain,
            topic,
            item,
            note,
            summary,
            1.0,
            0.95,
            False,
            None,
            embedding,
        )

    async def search_memories(
        self,
        *,
        user_id: str,
        query: str,
        limit: int = 6,
        min_score: float = 0.2,
    ) -> List[MemoryRecord]:
        rows, _ = await self.search_memories_with_event(
            user_id=user_id,
            query=query,
            session_id=None,
            channel=None,
            limit=limit,
            min_score=min_score,
        )
        return rows

    async def search_memories_with_event(
        self,
        *,
        user_id: str,
        query: str,
        session_id: Optional[str] = None,
        channel: Optional[str] = None,
        limit: int = 6,
        min_score: float = 0.2,
    ) -> tuple[List[MemoryRecord], Optional[int]]:
        if not self.enabled:
            return [], None

        q = _norm_text(query, max_chars=600)
        if not q:
            return [], None

        started = time.perf_counter()
        rows: List[MemoryRecord] = []
        used_vector = False
        fallback_to_text = False
        effective_result_count = 0

        vector = await self._embed(q)
        if vector and self._vector_supported and self._use_vector_column:
            used_vector = True
            rows = await asyncio.to_thread(
                self._search_vector_sync,
                user_id,
                vector,
                max(1, int(limit)),
                float(min_score),
            )
            effective_result_count = len(rows)
        if not rows:
            fallback_to_text = used_vector
            rows, text_hit = await asyncio.to_thread(
                self._search_text_with_meta_sync,
                user_id,
                q,
                max(1, int(limit)),
            )
            effective_result_count = len(rows) if text_hit else 0

        latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        top_score = float(rows[0].score) if rows and effective_result_count > 0 else None
        retrieval_id: Optional[int] = None
        try:
            retrieval_id = await asyncio.to_thread(
                self._log_retrieval_event_sync,
                user_id,
                session_id,
                channel,
                q,
                effective_result_count,
                top_score,
                latency_ms,
                used_vector,
                fallback_to_text,
            )
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to log retrieval event: %s", e)

        return rows, retrieval_id

    async def list_memories(
        self,
        *,
        user_id: str,
        tier: Optional[str] = None,
        limit: int = 20,
    ) -> List[MemoryRecord]:
        if not self.enabled:
            return []
        return await asyncio.to_thread(self._list_memories_sync, user_id, tier, max(1, min(100, int(limit))))

    async def get_memory(self, *, user_id: str, memory_id: int) -> Optional[MemoryRecord]:
        if not self.enabled:
            return None
        return await asyncio.to_thread(self._get_memory_sync, user_id, int(memory_id))

    async def forget_memory(self, *, user_id: str, memory_id: int) -> bool:
        if not self.enabled:
            return False
        return await asyncio.to_thread(self._forget_memory_sync, user_id, int(memory_id))

    async def set_pinned(self, *, user_id: str, memory_id: int, pinned: bool) -> bool:
        if not self.enabled:
            return False
        return await asyncio.to_thread(self._set_pinned_sync, user_id, int(memory_id), bool(pinned))

    async def share_memory_as_skill(self, *, user_id: str, memory_id: int, skill_name: str) -> Optional[str]:
        logger.info("Cross-user memory sharing is disabled (user=%s, memory_id=%s)", user_id, memory_id)
        return None

    async def list_shared_skills(self, *, limit: int = 30) -> List[MemoryRecord]:
        return []

    async def record_retrieval_feedback(
        self,
        *,
        user_id: str,
        retrieval_id: int,
        feedback: str,
        note: Optional[str] = None,
    ) -> bool:
        if not self.enabled:
            return False
        fb = str(feedback or "").strip().lower()
        if fb not in {"good", "bad"}:
            return False
        safe_note = _norm_text(note or "", max_chars=300) or None
        return await asyncio.to_thread(
            self._record_retrieval_feedback_sync,
            str(user_id),
            int(retrieval_id),
            fb,
            safe_note,
        )

    async def retrieval_stats(self, *, user_id: str, days: int = 7) -> Dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        span_days = max(1, min(90, int(days)))
        out = await asyncio.to_thread(self._retrieval_stats_sync, str(user_id), span_days)
        out["enabled"] = True
        out["days"] = span_days
        return out

    async def recent_retrieval_events(self, *, user_id: str, limit: int = 10) -> List[RetrievalEvent]:
        if not self.enabled:
            return []
        return await asyncio.to_thread(
            self._recent_retrieval_events_sync,
            str(user_id),
            max(1, min(50, int(limit))),
        )

    def _classify_type(self, user_text: str, assistant_text: str) -> tuple[str, float, float]:
        text = (user_text + "\n" + assistant_text).lower()
        if re.search(r"\b(以后|记住|默认|preference|prefer|always)\b", text):
            return ("preference", 0.9, 0.85)
        if "```" in text or re.search(r"\b(step|步骤|流程|run|command|命令)\b", text):
            return ("procedure", 0.8, 0.8)
        if re.search(r"\b(env|environment|系统|版本|路径|配置)\b", text):
            return ("env", 0.75, 0.75)
        return ("turn", 0.55, 0.7)

    def _initial_tier(self, memory_type: str, importance: float) -> str:
        if memory_type in {"preference", "procedure"} or importance >= 0.85:
            return "mid"
        return "short"

    def _classify_tree(self, user_text: str, assistant_text: str) -> tuple[str, str, str]:
        text = (user_text + "\n" + assistant_text).lower()
        domain = self.default_domain
        if re.search(r"\b(python|pytest|java|go|rust|typescript|node|git|sql|docker|k8s)\b", text):
            domain = "engineering"
        elif re.search(r"\b(deploy|systemd|linux|server|infra|ops)\b", text):
            domain = "operations"
        elif re.search(r"\b(write|summary|translate|文案|总结|翻译)\b", text):
            domain = "language"

        topic = self.default_topic
        if "test" in text or "pytest" in text:
            topic = "testing"
        elif "deploy" in text or "systemd" in text:
            topic = "deployment"
        elif "memory" in text or "记忆" in text:
            topic = "memory"
        elif "model" in text or "agent" in text:
            topic = "agent-config"

        words = re.findall(r"[a-zA-Z0-9_.-]+", user_text)[:4]
        item = "-".join(words).lower() if words else "item"
        item = re.sub(r"[^a-z0-9_.-]+", "-", item).strip("-") or "item"
        return domain, topic, item

    @staticmethod
    def _build_summary(user_text: str, assistant_text: str, domain: str, topic: str) -> str:
        u = _norm_text(user_text, max_chars=90)
        a = _norm_text(assistant_text, max_chars=90)
        return f"[{domain}/{topic}] U:{u} A:{a}".strip()

    async def _embed(self, text: str) -> Optional[List[float]]:
        if not self.embedding_enabled or not self._vector_supported:
            return None
        if not self.embedder.is_configured():
            return None
        return await self.embedder.embed(text)

    def _insert_memory_sync(
        self,
        owner_user_id: str,
        scope_id: Optional[str],
        session_id: Optional[str],
        channel: Optional[str],
        tier: str,
        memory_type: str,
        domain: str,
        topic: str,
        item: str,
        content: str,
        summary: str,
        importance: float,
        confidence: float,
        is_shared_skill: bool,
        skill_name: Optional[str],
        embedding: Optional[List[float]],
    ) -> Optional[int]:
        content_hash = _hash_text(owner_user_id, memory_type, content, skill_name or "")
        importance = _clamp(float(importance), 0.0, 1.0)
        confidence = _clamp(float(confidence), 0.0, 1.0)

        with self._conn() as conn:
            with conn.cursor() as cur:
                if self._vector_supported:
                    if not self._use_vector_column:
                        self._vector_supported = False
                if self._vector_supported and self._use_vector_column:
                    emb_value = _vector_literal(embedding) if embedding else None
                    cur.execute(
                        """
                        INSERT INTO memory_items (
                            owner_user_id, source_scope_id, session_id, channel, tier, memory_type,
                            domain, topic, item, content, summary, importance, confidence,
                            pinned, is_shared_skill, skill_name, content_hash, embedding
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            FALSE, %s, %s, %s, %s::vector
                        )
                        ON CONFLICT ON CONSTRAINT memory_unique_uniq
                        DO UPDATE SET
                            summary = EXCLUDED.summary,
                            content = EXCLUDED.content,
                            importance = GREATEST(memory_items.importance, EXCLUDED.importance),
                            confidence = GREATEST(memory_items.confidence, EXCLUDED.confidence),
                            updated_at = NOW(),
                            embedding = COALESCE(EXCLUDED.embedding, memory_items.embedding)
                        RETURNING id
                        """,
                        (
                            owner_user_id,
                            scope_id,
                            session_id,
                            channel,
                            tier,
                            memory_type,
                            domain,
                            topic,
                            item,
                            content,
                            summary,
                            importance,
                            confidence,
                            bool(is_shared_skill),
                            skill_name,
                            content_hash,
                            emb_value,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO memory_items (
                            owner_user_id, source_scope_id, session_id, channel, tier, memory_type,
                            domain, topic, item, content, summary, importance, confidence,
                            pinned, is_shared_skill, skill_name, content_hash, embedding_text
                        )
                        VALUES (
                            %s, %s, %s, %s, %s, %s,
                            %s, %s, %s, %s, %s, %s, %s,
                            FALSE, %s, %s, %s, %s
                        )
                        ON CONFLICT ON CONSTRAINT memory_unique_uniq
                        DO UPDATE SET
                            summary = EXCLUDED.summary,
                            content = EXCLUDED.content,
                            importance = GREATEST(memory_items.importance, EXCLUDED.importance),
                            confidence = GREATEST(memory_items.confidence, EXCLUDED.confidence),
                            updated_at = NOW()
                        RETURNING id
                        """,
                        (
                            owner_user_id,
                            scope_id,
                            session_id,
                            channel,
                            tier,
                            memory_type,
                            domain,
                            topic,
                            item,
                            content,
                            summary,
                            importance,
                            confidence,
                            bool(is_shared_skill),
                            skill_name,
                            content_hash,
                            None,
                        ),
                    )
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else None

    @staticmethod
    def _row_to_record(row: Iterable[Any]) -> MemoryRecord:
        values = list(row)
        return MemoryRecord(
            memory_id=int(values[0]),
            owner_user_id=str(values[1]),
            tier=str(values[2]),
            memory_type=str(values[3]),
            domain=str(values[4]),
            topic=str(values[5]),
            item=str(values[6]),
            summary=str(values[7]),
            content=str(values[8]),
            importance=float(values[9]),
            confidence=float(values[10]),
            pinned=bool(values[11]),
            is_shared_skill=bool(values[12]),
            skill_name=values[13],
            access_count=int(values[14]),
            score=float(values[15] or 0.0),
            created_at=values[16],
            updated_at=values[17],
        )

    def _search_vector_sync(self, user_id: str, vector: List[float], limit: int, min_score: float) -> List[MemoryRecord]:
        vector_lit = _vector_literal(vector)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                           summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                           access_count, (1 - (embedding <=> %s::vector)) AS score, created_at, updated_at
                    FROM memory_items
                    WHERE is_deleted = FALSE
                      AND embedding IS NOT NULL
                      AND (owner_user_id = %s OR owner_user_id = %s)
                    ORDER BY embedding <=> %s::vector ASC, pinned DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (vector_lit, user_id, self.SYSTEM_OWNER, vector_lit, limit),
                )
                rows = [self._row_to_record(row) for row in cur.fetchall()]
                filtered = [row for row in rows if row.score >= min_score or row.pinned]
                if filtered:
                    self._touch_rows_sync(cur, filtered)
                    conn.commit()
                return filtered

    def _search_text_sync(self, user_id: str, query: str, limit: int) -> List[MemoryRecord]:
        rows, _ = self._search_text_with_meta_sync(user_id, query, limit)
        return rows

    def _search_text_with_meta_sync(self, user_id: str, query: str, limit: int) -> tuple[List[MemoryRecord], bool]:
        """Return rows and whether there was a real query match (vs fallback rows)."""
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                           summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                           access_count, ts_rank(search_tsv, plainto_tsquery('simple', %s)) AS score,
                           created_at, updated_at
                    FROM memory_items
                    WHERE is_deleted = FALSE
                      AND (owner_user_id = %s OR owner_user_id = %s)
                      AND search_tsv @@ plainto_tsquery('simple', %s)
                    ORDER BY pinned DESC, score DESC, access_count DESC, updated_at DESC
                    LIMIT %s
                    """,
                    (query, user_id, self.SYSTEM_OWNER, query, limit),
                )
                rows = [self._row_to_record(row) for row in cur.fetchall()]
                matched = bool(rows)
                if not matched:
                    cur.execute(
                        """
                        SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                               summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                               access_count, 0.0 AS score, created_at, updated_at
                        FROM memory_items
                        WHERE is_deleted = FALSE
                          AND (owner_user_id = %s OR owner_user_id = %s)
                        ORDER BY pinned DESC, access_count DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (user_id, self.SYSTEM_OWNER, limit),
                    )
                    rows = [self._row_to_record(row) for row in cur.fetchall()]
                if rows:
                    self._touch_rows_sync(cur, rows)
                    conn.commit()
                return rows, matched

    def _touch_rows_sync(self, cur, rows: List[MemoryRecord]) -> None:
        ids = [int(row.memory_id) for row in rows]
        if not ids:
            return
        cur.execute(
            """
            UPDATE memory_items
            SET access_count = access_count + 1,
                last_accessed_at = NOW(),
                updated_at = NOW(),
                tier = CASE
                    WHEN pinned = TRUE THEN 'long'
                    WHEN access_count + 1 >= %s THEN 'long'
                    WHEN access_count + 1 >= %s AND tier = 'short' THEN 'mid'
                    ELSE tier
                END
            WHERE id = ANY(%s)
            """,
            (self.promote_mid_to_long, self.promote_short_to_mid, ids),
        )

    def _list_memories_sync(self, user_id: str, tier: Optional[str], limit: int) -> List[MemoryRecord]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                if tier and tier != "all":
                    cur.execute(
                        """
                        SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                               summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                               access_count, 0.0 AS score, created_at, updated_at
                        FROM memory_items
                        WHERE is_deleted = FALSE
                          AND tier = %s
                          AND (owner_user_id = %s OR owner_user_id = %s)
                        ORDER BY pinned DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (tier, user_id, self.SYSTEM_OWNER, limit),
                    )
                else:
                    cur.execute(
                        """
                        SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                               summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                               access_count, 0.0 AS score, created_at, updated_at
                        FROM memory_items
                        WHERE is_deleted = FALSE
                          AND (owner_user_id = %s OR owner_user_id = %s)
                        ORDER BY pinned DESC, updated_at DESC
                        LIMIT %s
                        """,
                        (user_id, self.SYSTEM_OWNER, limit),
                    )
                return [self._row_to_record(row) for row in cur.fetchall()]

    def _get_memory_sync(self, user_id: str, memory_id: int) -> Optional[MemoryRecord]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                           summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                           access_count, 0.0 AS score, created_at, updated_at
                    FROM memory_items
                    WHERE id = %s
                      AND is_deleted = FALSE
                      AND (owner_user_id = %s OR owner_user_id = %s)
                    """,
                    (memory_id, user_id, self.SYSTEM_OWNER),
                )
                row = cur.fetchone()
                return self._row_to_record(row) if row else None

    def _forget_memory_sync(self, user_id: str, memory_id: int) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE memory_items
                    SET is_deleted = TRUE, updated_at = NOW()
                    WHERE id = %s
                      AND owner_user_id = %s
                      AND is_deleted = FALSE
                    """,
                    (memory_id, user_id),
                )
                changed = cur.rowcount > 0
                conn.commit()
                return changed

    def _set_pinned_sync(self, user_id: str, memory_id: int, pinned: bool) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE memory_items
                    SET pinned = %s,
                        tier = CASE WHEN %s THEN 'long' ELSE tier END,
                        updated_at = NOW()
                    WHERE id = %s
                      AND owner_user_id = %s
                      AND is_deleted = FALSE
                    """,
                    (pinned, pinned, memory_id, user_id),
                )
                changed = cur.rowcount > 0
                conn.commit()
                return changed

    def _log_retrieval_event_sync(
        self,
        user_id: str,
        session_id: Optional[str],
        channel: Optional[str],
        query: str,
        result_count: int,
        top_score: Optional[float],
        latency_ms: int,
        used_vector: bool,
        fallback_to_text: bool,
    ) -> Optional[int]:
        query_text = _norm_text(query, max_chars=600)
        query_hash = _hash_text(user_id, query_text)
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO memory_retrieval_events (
                        owner_user_id, session_id, channel, query, query_hash,
                        result_count, top_score, latency_ms, used_vector, fallback_to_text
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING id
                    """,
                    (
                        user_id,
                        session_id,
                        channel,
                        query_text,
                        query_hash,
                        max(0, int(result_count)),
                        float(top_score) if top_score is not None else None,
                        max(0, int(latency_ms)),
                        bool(used_vector),
                        bool(fallback_to_text),
                    ),
                )
                row = cur.fetchone()
                conn.commit()
                return int(row[0]) if row else None

    def _mark_retrieval_context_injected_sync(self, retrieval_id: int, user_id: str, injected_count: int) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE memory_retrieval_events
                    SET context_injected = TRUE,
                        injected_count = GREATEST(0, %s),
                        updated_at = NOW()
                    WHERE id = %s
                      AND owner_user_id = %s
                    """,
                    (int(injected_count), int(retrieval_id), str(user_id)),
                )
                changed = cur.rowcount > 0
                conn.commit()
                return changed

    def _record_retrieval_feedback_sync(
        self,
        user_id: str,
        retrieval_id: int,
        feedback: str,
        note: Optional[str],
    ) -> bool:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE memory_retrieval_events
                    SET feedback = %s,
                        feedback_note = %s,
                        updated_at = NOW()
                    WHERE id = %s
                      AND owner_user_id = %s
                    """,
                    (str(feedback), note, int(retrieval_id), str(user_id)),
                )
                changed = cur.rowcount > 0
                conn.commit()
                return changed

    def _retrieval_stats_sync(self, user_id: str, days: int) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "total_queries": 0,
            "hit_queries": 0,
            "context_injected_queries": 0,
            "avg_result_count": 0.0,
            "avg_latency_ms": 0.0,
            "vector_queries": 0,
            "feedback_good": 0,
            "feedback_bad": 0,
            "hit_rate": 0.0,
            "context_inject_rate": 0.0,
            "vector_usage_rate": 0.0,
            "feedback_coverage": 0.0,
            "positive_feedback_rate": 0.0,
        }
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT
                        COUNT(*)::BIGINT AS total_queries,
                        COALESCE(SUM((result_count > 0)::INT), 0)::BIGINT AS hit_queries,
                        COALESCE(SUM((context_injected)::INT), 0)::BIGINT AS context_injected_queries,
                        COALESCE(AVG(result_count), 0.0)::DOUBLE PRECISION AS avg_result_count,
                        COALESCE(AVG(latency_ms), 0.0)::DOUBLE PRECISION AS avg_latency_ms,
                        COALESCE(SUM((used_vector)::INT), 0)::BIGINT AS vector_queries,
                        COALESCE(SUM((feedback = 'good')::INT), 0)::BIGINT AS feedback_good,
                        COALESCE(SUM((feedback = 'bad')::INT), 0)::BIGINT AS feedback_bad
                    FROM memory_retrieval_events
                    WHERE owner_user_id = %s
                      AND created_at >= NOW() - (%s::TEXT || ' days')::INTERVAL
                    """,
                    (str(user_id), int(days)),
                )
                row = cur.fetchone()
        if not row:
            return out

        out["total_queries"] = int(row[0] or 0)
        out["hit_queries"] = int(row[1] or 0)
        out["context_injected_queries"] = int(row[2] or 0)
        out["avg_result_count"] = float(row[3] or 0.0)
        out["avg_latency_ms"] = float(row[4] or 0.0)
        out["vector_queries"] = int(row[5] or 0)
        out["feedback_good"] = int(row[6] or 0)
        out["feedback_bad"] = int(row[7] or 0)

        total = max(0, int(out["total_queries"]))
        feedback_total = int(out["feedback_good"]) + int(out["feedback_bad"])
        if total > 0:
            out["hit_rate"] = float(out["hit_queries"]) / float(total)
            out["context_inject_rate"] = float(out["context_injected_queries"]) / float(total)
            out["vector_usage_rate"] = float(out["vector_queries"]) / float(total)
            out["feedback_coverage"] = float(feedback_total) / float(total)
        if feedback_total > 0:
            out["positive_feedback_rate"] = float(out["feedback_good"]) / float(feedback_total)
        return out

    def _recent_retrieval_events_sync(self, user_id: str, limit: int) -> List[RetrievalEvent]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, query, result_count, top_score, latency_ms,
                           context_injected, feedback, created_at
                    FROM memory_retrieval_events
                    WHERE owner_user_id = %s
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (str(user_id), int(limit)),
                )
                rows = cur.fetchall()
        return [
            RetrievalEvent(
                retrieval_id=int(row[0]),
                query=str(row[1] or ""),
                result_count=int(row[2] or 0),
                top_score=float(row[3]) if row[3] is not None else None,
                latency_ms=int(row[4] or 0),
                context_injected=bool(row[5]),
                feedback=(str(row[6]) if row[6] else None),
                created_at=row[7],
            )
            for row in rows
        ]

    def _share_memory_as_skill_sync(self, user_id: str, memory_id: int, skill_slug: str) -> Optional[str]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, content, summary FROM memory_items
                    WHERE id = %s AND owner_user_id = %s AND is_deleted = FALSE
                    """,
                    (memory_id, user_id),
                )
                row = cur.fetchone()
                if not row:
                    return None

                cur.execute(
                    """
                    SELECT id FROM memory_items
                    WHERE is_deleted = FALSE
                      AND is_shared_skill = TRUE
                      AND skill_name = %s
                      AND owner_user_id <> %s
                    LIMIT 1
                    """,
                    (skill_slug, user_id),
                )
                conflict = cur.fetchone()
                if conflict:
                    return None

                cur.execute(
                    """
                    UPDATE memory_items
                    SET is_shared_skill = TRUE,
                        skill_name = %s,
                        tier = 'long',
                        memory_type = 'skill',
                        updated_at = NOW()
                    WHERE id = %s
                    """,
                    (skill_slug, memory_id),
                )
                conn.commit()
                content = str(row[1] or "")
                summary = str(row[2] or "")
                return self._skill_markdown(skill_slug, summary, content, owner_user_id=user_id)

    def _list_shared_skills_sync(self, limit: int) -> List[MemoryRecord]:
        with self._conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT id, owner_user_id, tier, memory_type, domain, topic, item,
                           summary, content, importance, confidence, pinned, is_shared_skill, skill_name,
                           access_count, 0.0 AS score, created_at, updated_at
                    FROM memory_items
                    WHERE is_deleted = FALSE
                      AND is_shared_skill = TRUE
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                )
                return [self._row_to_record(row) for row in cur.fetchall()]

    @staticmethod
    def _skill_markdown(skill_slug: str, summary: str, content: str, owner_user_id: str) -> str:
        title = skill_slug.replace("-", " ").strip() or "Shared Skill"
        return (
            f"# {title}\n\n"
            f"- source_user: `{owner_user_id}`\n"
            f"- summary: {summary[:200]}\n\n"
            "## Instructions\n\n"
            f"{content}\n"
        )

    def _write_skill_file_sync(self, skill_slug: str, markdown: str) -> None:
        skill_dir = self.skill_export_dir / skill_slug
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_path = skill_dir / "SKILL.md"
        skill_path.write_text(markdown, encoding="utf-8")

    async def _env_probe_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._run_env_probe_once()
            except Exception as e:  # noqa: BLE001
                logger.warning("Memory env probe failed: %s", e)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=float(self.env_probe_interval))
            except asyncio.TimeoutError:
                continue

    async def _run_env_probe_once(self) -> None:
        if not self.env_probe_commands:
            return
        for cmd in self.env_probe_commands:
            output = await asyncio.to_thread(self._exec_probe_cmd_sync, cmd)
            if not output:
                continue
            summary = f"[env] {' '.join(cmd)} -> {output.splitlines()[0][:120]}"
            if self.reject_sensitive and self._contains_sensitive(output):
                continue
            emb = await self._embed(summary + "\n" + output)
            await asyncio.to_thread(
                self._insert_memory_sync,
                self.SYSTEM_OWNER,
                "system:env",
                "env-probe",
                "system",
                "long",
                "env",
                "operations",
                "environment",
                _safe_skill_slug(" ".join(cmd)),
                output,
                summary,
                0.8,
                0.8,
                False,
                None,
                emb,
            )
        self._last_probe_at = _utcnow()

    def _exec_probe_cmd_sync(self, cmd: List[str]) -> str:
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.env_probe_timeout,
                check=False,
            )
            payload = (proc.stdout or "") + ("\n" + proc.stderr if proc.stderr else "")
            payload = payload.strip()
            if not payload:
                payload = f"(exit={proc.returncode})"
            if len(payload) > self.env_probe_max_chars:
                payload = payload[: self.env_probe_max_chars] + "..."
            return f"$ {' '.join(cmd)}\n{payload}"
        except Exception as e:  # noqa: BLE001
            return f"$ {' '.join(cmd)}\nerror: {e}"
