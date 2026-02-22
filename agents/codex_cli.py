"""
Codex CLI agent adapter — thin wrapper over StreamingCliAgent.
"""
from typing import Dict, List, Optional

from agents.streaming_cli import StreamingCliAgent


class CodexAgent(StreamingCliAgent):
    """Codex CLI adapter using the shared streaming subprocess logic."""

    agent_label = "Codex"
    _SKIP_GIT_REPO_CHECK = "--skip-git-repo-check"
    _FULL_AUTO = "--full-auto"
    _DANGEROUS_BYPASS = "--dangerously-bypass-approvals-and-sandbox"

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

    def _finalize_args(self, args: List[str], *, run_as_root: bool = False) -> List[str]:
        out = list(args)
        # Highest-privilege mode: only in system runtime + sudo-on path.
        if str(self.runtime_mode).lower() not in {"system", "sys"} or not bool(run_as_root):
            return out
        out = [arg for arg in out if arg != self._FULL_AUTO]
        if self._DANGEROUS_BYPASS not in out:
            out.append(self._DANGEROUS_BYPASS)
        return out
