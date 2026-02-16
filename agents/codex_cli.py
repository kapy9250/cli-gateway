"""
Codex CLI agent adapter — thin wrapper over StreamingCliAgent.
"""
from agents.streaming_cli import StreamingCliAgent


class CodexAgent(StreamingCliAgent):
    """Codex CLI adapter using the shared streaming subprocess logic."""

    agent_label = "Codex"
