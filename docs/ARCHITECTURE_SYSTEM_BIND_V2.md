# System/Sys-Executor Strong-Bind Architecture (V2)

## 1. Problem Statement

Current implementation already has role separation:

- `session` instance handles normal user chat workflows
- `system` instance handles `/sys` and `/sysauth`
- `sys-executor` handles privileged operations via local Unix socket

But there are two security gaps:

1. `/sys` has local-executor fallback path in gateway code, so not all operations are forced through `sys-executor`.
2. `sys-executor` caller identity is primarily checked by peer UID; if UID/socket ownership is misconfigured, a local caller may still access readable operations.

## 2. Target Principles

1. **Single base gateway code**: keep one gateway service codebase, run in `session` or `system` mode.
2. **Single privileged execution point**: all `/sys` actions must execute only in `sys-executor`.
3. **Strong caller binding**: `sys-executor` must accept requests only from expected `system` service instance identity.
4. **Uniform authorization semantics**: all `/sys` actions require short-lived grant token minted after 2FA approval.
5. **Low maintenance cost**: share action schema and command parsing; keep privileged module thin.

## 3. Component Responsibilities

### 3.1 Gateway (`main.py`, mode=session/system)

- Channel integration (telegram/discord/email)
- Model integration (codex/claude/gemini)
- Session/workspace/files lifecycle
- Auth whitelist and `system_admin` checks
- 2FA enrollment and approval (`/sysauth`)
- Grant minting for `/sys`
- Audit event emission

### 3.2 Sys-Executor (`system_service_main.py`, root)

- Listen on Unix socket
- Verify peer identity:
  - peer UID allowlist
  - peer PID cgroup/unit allowlist (e.g. `cli-gateway-system@ops-a.service`)
- Verify grant token for **all** ops
- Execute structured system actions
- Return structured result only

## 4. Security Contract

### 4.1 No Local Privileged Fallback

In `system` mode, `/sys` command must fail if remote client is unavailable.  
Gateway local `SystemExecutor` call path is removed from `/sys`.

### 4.2 Strong Bind (System -> Sys-Executor)

`sys-executor` request accept policy:

1. socket file mode/owner permit only intended gateway runtime user
2. peer UID must be in `allowed_peer_uids`
3. peer PID must resolve to expected systemd unit in `allowed_peer_units`
4. request must include valid, unexpired, one-time grant

All four must pass.

### 4.3 Grant-For-All-Ops

`sys-executor` no longer has “public read” operations.  
`journal`, `read_file`, and all write ops require grant.

## 5. Configuration Additions

Proposed `system_service` config keys:

- `enforce_peer_uid_allowlist: true`
- `allowed_peer_uids: [<gateway_system_uid>]`
- `enforce_peer_unit_allowlist: true`
- `allowed_peer_units: ["cli-gateway-system@ops-a.service"]`
- `require_grant_for_all_ops: true`

Existing keys remain:

- `socket_path`, `socket_mode`, `socket_uid`, `socket_gid`
- `grant_secret`, `grant_ttl_seconds`
- `request_timeout_seconds`, `max_request_bytes`

## 6. Data and Control Flow

1. User sends `/sys ...` to `system` bot.
2. Gateway validates `system_admin`.
3. Gateway creates challenge if needed; user approves with `/sysauth approve`.
4. Gateway consumes approval, mints one-time grant token.
5. Gateway sends action + grant to `sys-executor`.
6. `sys-executor` verifies peer UID + unit + grant.
7. `sys-executor` executes action and returns result.
8. Gateway formats response and writes audit log.

## 7. Migration Strategy

1. Introduce new checks behind config flags (default secure in `system` templates).
2. Update tests for “all ops require grant”.
3. Remove local fallback in `/sys`.
4. Validate on canary host:
  - expected unit can call
  - local shell process cannot call
  - missing grant always denied
5. Roll out with updated systemd/config bootstrap.

