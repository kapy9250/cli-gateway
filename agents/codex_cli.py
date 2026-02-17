"""
Codex CLI agent adapter — thin wrapper over StreamingCliAgent.
"""
from typing import Dict, Optional

from agents.streaming_cli import StreamingCliAgent


class CodexAgent(StreamingCliAgent):
    """Codex CLI adapter using the shared streaming subprocess logic."""

    agent_label = "Codex"
    _SKIP_GIT_REPO_CHECK = "--skip-git-repo-check"

    def _build_args(
        self,
        message: str,
        session_id: str,
        model: Optional[str] = None,
        params: Optional[Dict[str, str]] = None,
    ) -> list[str]:
        """Ensure Codex can run in non-repo workspaces by default."""
        args = super()._build_args(message, session_id, model=model, params=params)
        if self._SKIP_GIT_REPO_CHECK not in args:
            args.append(self._SKIP_GIT_REPO_CHECK)
        return args
