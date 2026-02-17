"""Client for privileged system service over local Unix socket."""

from __future__ import annotations

import asyncio
import json
from typing import Dict, Optional


class SystemServiceClient:
    def __init__(self, socket_path: str, timeout_seconds: float = 10.0):
        self.socket_path = str(socket_path)
        self.timeout_seconds = float(timeout_seconds)

    async def execute(self, user_id: str, action: dict, grant_token: Optional[str] = None) -> Dict[str, object]:
        req = {
            "user_id": str(user_id),
            "action": action or {},
        }
        if grant_token:
            req["grant"] = str(grant_token)

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_unix_connection(self.socket_path),
                timeout=self.timeout_seconds,
            )
        except Exception as e:
            return {"ok": False, "reason": f"connect_failed:{e}"}

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
                return {"ok": False, "reason": f"response_decode_failed:{e}"}
            if not isinstance(data, dict):
                return {"ok": False, "reason": "response_not_object"}
            return data
        except Exception as e:
            return {"ok": False, "reason": f"request_failed:{e}"}
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
