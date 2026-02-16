"""
Centralized constants for CLI Gateway.

Collects magic strings, limits, and compiled patterns that were previously
scattered across modules.
"""
import re

# ── Attachment limits ──
MAX_ATTACHMENT_SIZE_BYTES = 10 * 1024 * 1024  # 10 MB

# ── Message length limits per channel ──
MAX_MESSAGE_LENGTH = {
    "telegram": 4096,
    "discord": 2000,
    "email": None,  # no hard limit
}

# ── Gateway commands (intercepted by Router, not forwarded to agent) ──
GATEWAY_COMMANDS = frozenset({
    '/start',
    '/help',
    '/agent',
    '/sessions',
    '/kill',
    '/current',
    '/switch',
    '/model',
    '/param',
    '/params',
    '/reset',
    '/files',
    '/download',
    '/cancel',
    '/name',
    '/history',
})

# ── Auto-retry ──
MAX_AGENT_RETRIES = 1  # Number of automatic retries on transient agent failure

# ── History ──
MAX_HISTORY_ENTRIES = 20  # Max prompt/response pairs to keep per session

# ── CLI output format ──
CLI_OUTPUT_FORMAT_FLAG = "--output-format"
CLI_OUTPUT_FORMAT_JSON = "json"

# ── Email session marker ──
# HTML comment format — invisible in both plain-text and HTML rendering,
# virtually impossible for a user to type accidentally.
SESSION_MARKER_TEMPLATE = "<!-- clawdbot-session:{session_id} -->"
SESSION_MARKER_RE = re.compile(r'<!-- clawdbot-session:([a-f0-9-]{6,}) -->')

# ── Streaming update interval (seconds) ──
STREAM_UPDATE_INTERVAL = 2.0
