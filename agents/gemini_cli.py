"""
Gemini CLI agent adapter — thin wrapper over StreamingCliAgent.
"""
from typing import List

from agents.streaming_cli import StreamingCliAgent


class GeminiAgent(StreamingCliAgent):
    """Gemini CLI adapter using the shared streaming subprocess logic."""

    agent_label = "Gemini"
    _YOLO = "--yolo"
    _YOLO_SHORT = "-y"
    _APPROVAL_MODE = "--approval-mode"
    _APPROVAL_MODE_PREFIX = "--approval-mode="
    _APPROVAL_MODE_YOLO_FLAG = "--approval-mode=yolo"
    _SANDBOX_FALSE = "--sandbox=false"

    def _finalize_args(self, args: List[str], *, run_as_root: bool = False) -> List[str]:
        out = list(args)
        if str(self.runtime_mode).lower() not in {"system", "sys"} or not bool(run_as_root):
            return out

        normalized: List[str] = []
        i = 0
        while i < len(out):
            token = out[i]
            if token == self._APPROVAL_MODE:
                i += 2
                continue
            if token.startswith(self._APPROVAL_MODE_PREFIX):
                i += 1
                continue
            if token in {self._YOLO, self._YOLO_SHORT}:
                i += 1
                continue
            if token == "--sandbox":
                i += 2
                continue
            if token.startswith("--sandbox="):
                i += 1
                continue
            normalized.append(token)
            i += 1

        out = normalized
        out.append(self._APPROVAL_MODE_YOLO_FLAG)
        out.append(self._SANDBOX_FALSE)
        return out
