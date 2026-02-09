"""
Billing tracker - records per-interaction cost to dedicated billing files.

Billing files are stored outside the user-facing session workspace to prevent
users from accessing cost data directly.
"""
import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)


class BillingTracker:
    """Append-only billing log per session.

    Structure:
        {billing_dir}/
        ├── {session_id}.jsonl   # per-session billing entries
        └── ...

    Each line in a .jsonl file is a JSON object with fields:
        timestamp, session_id, user_id, channel, agent, model,
        input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
        cost_usd, cumulative_cost_usd, duration_ms
    """

    def __init__(self, billing_dir: str = "./data/billing"):
        self.billing_dir = Path(billing_dir)
        self.billing_dir.mkdir(parents=True, exist_ok=True)
        self._cumulative: dict = {}  # session_id -> cumulative cost
        self._lock = threading.Lock()
        self._load_cumulative()
        logger.info("BillingTracker initialized (dir=%s)", self.billing_dir)

    def _load_cumulative(self) -> None:
        """Load cumulative costs from existing billing files on startup."""
        for f in self.billing_dir.glob("*.jsonl"):
            session_id = f.stem
            total = 0.0
            try:
                for line in f.read_text(encoding="utf-8").splitlines():
                    if line.strip():
                        entry = json.loads(line)
                        total = entry.get("cumulative_cost_usd", total)
                self._cumulative[session_id] = total
            except Exception as e:
                logger.warning("Failed to load billing for %s: %s", session_id, e)

    def record(
        self,
        session_id: str,
        user_id: str,
        channel: str,
        agent_name: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_creation_tokens: int = 0,
        cost_usd: float = 0.0,
        duration_ms: int = 0,
    ) -> dict:
        """Record a billing entry and return it."""
        with self._lock:
            prev = self._cumulative.get(session_id, 0.0)
            cumulative = prev + cost_usd
            self._cumulative[session_id] = cumulative

        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "session_id": session_id,
            "user_id": user_id,
            "channel": channel,
            "agent": agent_name,
            "model": model,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "cache_read_tokens": cache_read_tokens,
            "cache_creation_tokens": cache_creation_tokens,
            "cost_usd": round(cost_usd, 8),
            "cumulative_cost_usd": round(cumulative, 8),
            "duration_ms": duration_ms,
        }

        filepath = self.billing_dir / f"{session_id}.jsonl"
        try:
            with open(filepath, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            logger.info(
                "Billing: session=%s agent=%s model=%s cost=$%.6f cumulative=$%.6f",
                session_id, agent_name, model, cost_usd, cumulative,
            )
        except Exception as e:
            logger.error("Failed to write billing for session %s: %s", session_id, e)

        return entry

    def get_session_total(self, session_id: str) -> float:
        """Get cumulative cost for a session."""
        return self._cumulative.get(session_id, 0.0)
