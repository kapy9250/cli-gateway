# CHANGELOG

## 2026-02-17 — System Mode Security Gate + Path Hardening

### Summary
Adds security hardening for privileged runtime and aligns tests/docs with the multi-instance + dual-mode architecture.

### Changes
- Enforced **global system-mode gate** in auth middleware:
  - when `runtime.mode=system`, only `system_admin_users` can access the bot (including non-command prompts)
- Hardened `SystemExecutor` path checks:
  - normalized path matching via canonicalized absolute paths
  - blocked `..` traversal and symlink-based escapes from `write_allowed_paths`
  - normalized sensitive-path detection for read operations
- Fixed `read_file` resource-control gaps:
  - user `max_bytes` is now clamped to configured `max_read_bytes`
  - file read is streaming (`limit + 1`) instead of full-file in-memory load
- Hardened audit logging privacy:
  - `/sys` audit events now redact raw `text/output/stderr/stdout` payloads
  - audit stores digest/size metadata instead of sensitive raw content
- Completed privilege revocation behavior:
  - `remove_user` now also revokes `system_admin` role
- Extended path namespacing:
  - `--namespace-paths` now includes `logging.audit.file`
- Added regression tests:
  - `tests/test_system_mode_security.py`
  - `tests/test_system_executor_security.py`
  - expanded `tests/test_auth.py` for `system_admin` role persistence/ops
- Updated docs/config consistency:
  - removed duplicated `codex`/`gemini` keys from `config.example.yaml`
  - refreshed `README.md` testing and roadmap sections
- Test hygiene / pre-commit cleanup:
  - replaced `from tests.conftest import ...` patterns with fixture injection to avoid import collisions
  - fixed async subprocess kill handling in `ClaudeCodeAgent` so pytest warnings are eliminated

## 2026-02-07 — Security, Stability, and Architecture Hardening

### Summary
This update closes all 24 issues identified during the multi-review code audit (Agent layer, Core/Router, Channel/Config).

### P0 Fixes (Critical)
- Added session-level concurrency control with per-session locks.
- Implemented real `cancel()` behavior for all CLI agents.
- Switched process management to process-group aware lifecycle:
  - `start_new_session=True`
  - SIGTERM/SIGKILL via `os.killpg()`
- Removed sensitive prompt/args logging from INFO logs (now sanitized metadata only).
- Fixed duplicated `codex/gemini` blocks in `config.example.yaml`.
- Added attachment cleanup to prevent temp-file disk leaks.
- Added chat-scope auth support (`allowed_chats`) in addition to user whitelist.
- Added configurable per-user rate limiting.

### P1 Fixes (Reliability / Maintainability)
- Added persistent auth state (`state_file`) for allowlist/admin changes.
- Added RBAC foundation (`admin_users`, `is_admin`, `add_admin`, `remove_admin`).
- Refactored duplicated agent lifecycle logic into `BaseAgent` helpers.
- Added retry/backoff + fallback behavior for message edit failures during streaming.
- Reworked fragile `kapy` command parsing to token-based normalization.
- Added stale-session workspace cleanup and managed workspace deletion on session destroy.
- Added atomic session-state save (`tmp + replace`) with save lock.
- Enforced `max_sessions_per_user` from config.
- Implemented inactive session cleanup (`cleanup_inactive_after_hours`) with periodic background task.
- Improved asyncio signal handling (`loop.add_signal_handler` + fallback).

### P2 Fixes (Quality)
- Safer text truncation path for markup-heavy messages.
- Plain-text fallback now strips markup before sending/editing.
- Telegram group handling now supports reply/mention/command routing.
- Startup banner now uses dynamic width (no overflow with long names/paths).
- Removed/avoided brittle inline patterns and reduced code repetition in router flow.

### Files Updated
- `agents/base.py`
- `agents/claude_code.py`
- `agents/codex_cli.py`
- `agents/gemini_cli.py`
- `channels/base.py`
- `channels/telegram.py`
- `config.example.yaml`
- `core/auth.py`
- `core/router.py`
- `core/session.py`
- `main.py`
- `utils/helpers.py`

### Notes
- This changelog reflects reviewed fixes already acknowledged by peer reviewers.
- Recommended next step: add/expand regression tests for cancellation, rate limits, session cleanup, and Telegram fallback behavior.
