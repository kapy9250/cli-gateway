# Test Cases - System/Sys-Executor Strong Bind (V2)

## 1. Goal

Validate that:

1. all `/sys` actions are routed to `sys-executor`
2. all `/sys` actions require grant (2FA approval path)
3. `sys-executor` only accepts calls from bound `system` service identity
4. security changes do not regress normal session mode behavior

## 2. Unit Tests

### U1 - `/sys` refuses local fallback

- Type: unit/integration (router + command)
- Setup: `runtime.mode=system`, `system_client=None`
- Action: invoke `/sys journal 10`
- Expect: fail with explicit bridge unavailable message; no local executor call

### U2 - grant required for journal

- Setup: remote client available, no challenge/grant
- Action: invoke `/sys journal 10`
- Expect: challenge required response; no remote call sent without challenge

### U3 - grant required for read_file

- Setup: same as U2
- Action: invoke `/sys read /etc/hosts`
- Expect: challenge required response

### U4 - grant required for docker/write ops

- Setup: same as U2
- Action: invoke `/sys docker ps`
- Expect: challenge required response

### U5 - approved challenge can mint grant and proceed

- Setup: challenge approved with valid TOTP
- Action: retry `/sys ... --challenge <id>`
- Expect: remote call includes `grant_token` and succeeds once

## 3. Sys-Executor Policy Tests

### P1 - peer UID allowlist

- Setup: allowed UID set to X
- Action: check `_is_peer_uid_allowed(X)` and `_is_peer_uid_allowed(Y)`
- Expect: X pass, Y fail

### P2 - peer unit allowlist parser

- Setup: mock `/proc/<pid>/cgroup` data
- Action: resolve unit name from pid
- Expect: expected unit extracted (e.g. `cli-gateway-system@ops-a.service`)

### P3 - unit allowlist reject unknown peer

- Setup: unit allowlist enabled, peer unit absent/unknown
- Action: process request
- Expect: denied with explicit reason

### P4 - combined UID + unit policy

- Setup: both allowlists enabled
- Action: test four combinations
- Expect: only `(uid ok, unit ok)` passes

### P5 - grant-for-all-ops

- Setup: `require_grant_for_all_ops=true`
- Action: `journal` request without grant
- Expect: denied (`grant_required`)

## 4. Bridge Integration Tests

### I1 - expected system caller accepted

- Setup: server configured with target UID + unit
- Action: call through normal `SystemServiceClient` path from bound process
- Expect: request accepted with valid grant

### I2 - local shell caller rejected

- Setup: same as I1
- Action: direct local unix socket call from non-bound process
- Expect: denied (`peer_uid_not_allowed` or `peer_unit_not_allowed`)

### I3 - replayed grant rejected

- Setup: one valid token used once
- Action: reuse token
- Expect: denied (`token_replayed`)

## 5. Regression Tests

### R1 - session mode unaffected

- Action: send normal non-system command in session instance
- Expect: existing behavior unchanged

### R2 - system mode admin gate unaffected

- Action: non-system-admin sends any message in system mode
- Expect: blocked

### R3 - 2FA enrollment flow unaffected

- Action: `/sysauth setup start`, `/sysauth setup verify <code>`
- Expect: success and persistence as before

## 6. Remote Validation on Host 208

### H1 - service topology

- Expect active:
  - `cli-gateway-session@user-main`
  - `cli-gateway-system@ops-a`
  - `cli-gateway-sys-executor@ops-a`

### H2 - socket ownership and peer policy

- Validate:
  - socket mode/owner/group
  - configured `allowed_peer_uids`
  - configured `allowed_peer_units`

### H3 - attack-path smoke test

- Action: local interactive user process attempts socket call without being bound unit
- Expect: denied

### H4 - happy-path smoke test

- Action: chat flow `/sysauth` + `/sys`
- Expect: system action succeeds only after approval

