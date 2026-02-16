"""
Gemini CLI agent adapter — thin wrapper over StreamingCliAgent.
"""
from agents.streaming_cli import StreamingCliAgent


class GeminiAgent(StreamingCliAgent):
    """Gemini CLI adapter using the shared streaming subprocess logic."""

    agent_label = "Gemini"
