"""Client for privileged system service over local Unix socket."""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator, Dict, Optional


class SystemServiceClient:
    def __init__(self, socket_path: str, timeout_seconds: float = 120.0):
        self.socket_path = str(socket_path)
        self.timeout_seconds = float(timeout_seconds)

    @staticmethod
    def _exc_text(err: Exception) -> str:
        text = str(err or "").strip()
        if text:
            return text
        return err.__class__.__name__

    @staticmethod
    def _build_request(user_id: str, action: dict, grant_token: Optional[str] = None) -> dict:
        req = {
            "user_id": str(user_id),
            "action": action or {},
        }
        if grant_token:
            req["grant"] = str(grant_token)
        return req

    @staticmethod
    def _to_done_frame(payload: Dict[str, object]) -> Dict[str, object]:
        data = dict(payload or {})
        data.setdefault("event", "done")
        return data

    async def execute(self, user_id: str, action: dict, grant_token: Optional[str] = None) -> Dict[str, object]:
        req = self._build_request(user_id, action, grant_token)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.timeout_seconds,
            )
        except Exception as e:
            return {"ok": False, "reason": f"connect_failed:{self._exc_text(e)}"}

        try:
            wire = json.dumps(req, ensure_ascii=False, separators=(",", ":")) + "\n"
            writer.write(wire.encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout_seconds)
            if not raw:
                return {"ok": False, "reason": "empty_response"}
            try:
                data = json.loads(raw.decode("utf-8"))
            except Exception as e:
                return {"ok": False, "reason": f"response_decode_failed:{self._exc_text(e)}"}
            if not isinstance(data, dict):
                return {"ok": False, "reason": "response_not_object"}
            return data
        except Exception as e:
            return {"ok": False, "reason": f"request_failed:{self._exc_text(e)}"}
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def execute_stream(
        self,
        user_id: str,
        action: dict,
        grant_token: Optional[str] = None,
    ) -> AsyncIterator[Dict[str, object]]:
        """Execute request and yield JSONL stream frames until terminal event."""
        req = self._build_request(user_id, action, grant_token)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.timeout_seconds,
            )
        except Exception as e:
            yield {"event": "error", "ok": False, "reason": f"connect_failed:{self._exc_text(e)}"}
            return

        try:
            wire = json.dumps(req, ensure_ascii=False, separators=(",", ":")) + "\n"
            writer.write(wire.encode("utf-8"))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout_seconds)

            while True:
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout_seconds)
                except Exception as e:
                    yield {"event": "error", "ok": False, "reason": f"request_failed:{self._exc_text(e)}"}
                    return
                if not raw:
                    yield {"event": "error", "ok": False, "reason": "empty_response"}
                    return
                try:
                    data = json.loads(raw.decode("utf-8"))
                except Exception as e:
                    yield {"event": "error", "ok": False, "reason": f"response_decode_failed:{self._exc_text(e)}"}
                    return
                if not isinstance(data, dict):
                    yield {"event": "error", "ok": False, "reason": "response_not_object"}
                    return
                frame = self._to_done_frame(data)
                yield frame
                event = str(frame.get("event", "")).strip().lower()
                if event in {"done", "error"}:
                    return
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
