# Implementation Plan - System/Sys-Executor Strong Bind (V2)

## 1. Scope

Deliver a stronger security model without creating another gateway codebase:

- keep one gateway service code, run in `session` or `system` mode
- make `sys-executor` the only privileged execution point
- enforce strong caller binding (`system` instance identity)
- require grant for all `/sys` operations

## 2. Phase Plan

### Phase 0 - Design and Planning (current)

- [x] Architecture design doc
- [x] Implementation plan doc
- [x] Test cases doc

Validation:

- docs created in `docs/`

Critical reflection:

- New design removes ambiguous fallback behavior.
- Strong bind must use both UID and systemd-unit checks; UID-only is insufficient.

### Phase 1 - Contract Changes in Code

Tasks:

- [ ] Remove local execution fallback from `/sys` in system mode
- [ ] Make `/sys` require approved challenge/grant for all ops
- [ ] Ensure gateway fails closed if remote bridge unavailable in system mode

Validation:

- targeted pytest for `/sys` command behavior

Critical reflection checkpoint:

- confirm user experience remains clear (challenge instructions still usable)
- confirm no command path bypasses grant requirement

### Phase 2 - Sys-Executor Strong Bind

Tasks:

- [ ] Add peer PID -> systemd unit extraction helper
- [ ] Add `allowed_peer_units` + `enforce_peer_unit_allowlist`
- [ ] Enforce both UID and unit allowlist checks
- [ ] Add `require_grant_for_all_ops` and enable in system profiles

Validation:

- unit tests for peer policy logic
- integration tests for socket bridge accept/reject

Critical reflection checkpoint:

- ensure implementation is Linux-safe and fail-closed on parse errors
- avoid overfitting to one distro path format

### Phase 3 - Config and Deployment Hardening

Tasks:

- [ ] Update `config.example.yaml` and docs
- [ ] Update bootstrap script defaults if needed
- [ ] Ensure ops config templates carry secure defaults

Validation:

- config validation tests
- dry-run `--validate-only`

Critical reflection checkpoint:

- verify secure defaults do not block intended startup path

### Phase 4 - End-to-End Verification and Rollout Notes

Tasks:

- [ ] Run full test suite
- [ ] Verify on 208 host: service states and security checks
- [ ] Write summary and residual risks

Validation:

- `pytest -q`
- remote smoke checks

Critical reflection checkpoint:

- compare expected vs actual behavior differences
- document any design adjustment decisions

## 3. Step Status Board

| Step ID | Description | Status | Verification Evidence | Reflection |
|---|---|---|---|---|
| S0 | Branch created from latest baseline | Done | branch `codex/system-executor-strong-bind` | baseline clean |
| S1 | Phase 0 docs completed | Done | `ARCHITECTURE_SYSTEM_BIND_V2.md` / `PLAN_SYSTEM_BIND_V2.md` / `TEST_CASES_SYSTEM_BIND_V2.md` | design and test scope aligned |
| S2 | Phase 1 contract code changes | Done | `pytest -q tests/test_sys_command_remote_bridge.py tests/test_system_mode_security.py` => `9 passed` | no local fallback remains in `/sys`; all ops now走 challenge/grant 流 |
| S3 | Phase 2 strong-bind code changes | Done | `pytest -q tests/test_system_service_bridge.py tests/test_system_service_main.py tests/test_sys_command_remote_bridge.py` => `22 passed` | strong-bind capability landed: peer unit allowlist + grant-for-all flag |
| S4 | Phase 3 config/deploy updates | Done | `pytest -q tests/test_ops_config.py tests/test_system_service_bridge.py tests/test_system_service_main.py tests/test_sys_command_remote_bridge.py` => `29 passed` | secure defaults moved into config/bootstrap; docs aligned |
| S5 | Phase 4 tests + remote verification | In Progress | local full suite `pytest -q` => `322 passed` | pending remote host verification |
