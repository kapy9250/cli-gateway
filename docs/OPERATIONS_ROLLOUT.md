# CLI Gateway Rollout Runbook

This runbook validates multi-instance deployment, dual permission modes, 2FA flows, and system ops controls.

## 1) Precheck

```bash
cd /opt/cli-gateway
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt

./.venv/bin/python main.py --config config.yaml --validate-only
./.venv/bin/python main.py --config config.yaml --instance-id canary --namespace-paths --validate-only
./.venv/bin/python system_service_main.py --config config.yaml --validate-only
```

Expected:
- runtime mode/instance rendered correctly
- paths are isolated by instance id

## 2) systemd Template Validation

```bash
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-session@.service
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-system@.service
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-sys-executor@.service
```

Expected:
- no syntax errors

## 3) Bootstrap Ops Config (Recommended)

```bash
./.venv/bin/python scripts/bootstrap_ops_config.py \
  --source-config /data/workspaces/cli-gateway/config.yaml \
  --privileged-config /etc/cli-gateway/ops-a.yaml \
  --output /etc/cli-gateway/ops-a.yaml \
  --instance-id ops-a \
  --health-port 18810 \
  --channel-profile telegram-only \
  --print-otpauth
```

Expected:
- `ops-a.yaml` contains full `session/agents/channels` + preserved privileged `system_ops/system_service`
- missing `two_factor.secrets` for `system_admin_users` are generated
- health port is unique for this instance

## 4) Start Session + Ops + Executor Instances

```bash
sudo systemctl daemon-reload
sudo systemctl disable --now cli-gateway.service || true
sudo systemctl enable --now cli-gateway-session@bot-a
sudo systemctl enable --now cli-gateway-system@ops-a
sudo systemctl enable --now cli-gateway-sys-executor@ops-a
sudo systemctl status cli-gateway-session@bot-a --no-pager -l
sudo systemctl status cli-gateway-system@ops-a --no-pager -l
sudo systemctl status cli-gateway-sys-executor@ops-a --no-pager -l
```

Note:
- `cli-gateway-system@ops-a` reads the same `%i.yaml` as the chat gateway and requires full `session/agents/channels` config.
- `cli-gateway-sys-executor@ops-a` can be validated independently with a minimal privileged config.

Expected:
- all services active
- separate config/state/workspace/log paths
- no Telegram `Conflict` errors caused by duplicate bot instances

## 5) Identity & Mode Gate

In chat:
- `kapy whoami`
- non-system instance send: `kapy sys journal 10`
- system instance (non-system-admin) send plain text: `hello`

Expected:
- `/whoami` shows role + mode
- session mode blocks system commands
- system mode blocks all access for non-system-admin users

## 6) 2FA Challenge Flow

In system instance chat:
- `kapy sysauth plan rotate-nginx`
- `kapy sysauth approve <challenge_id> <totp_code>`
- `kapy sysauth status <challenge_id>`

Expected:
- challenge created
- approval succeeds only with valid TOTP
- challenge status changes as expected

## 7) Sensitive Read Flow

In system instance chat:
- `kapy sys read /etc/shadow` (should require challenge)
- `kapy sysauth approve <id> <totp_code>`
- `kapy sys read /etc/shadow --challenge <id>`

Expected:
- sensitive read requires 2FA
- approved challenge can be consumed once

## 8) Write/Ops Flow

In system instance chat:
- `kapy sys cron list`
- `kapy sys docker ps -a`
- `kapy sys config write /etc/myapp.conf <base64_content>`

Expected:
- write/docker operations require challenge
- after approval operations execute and return result

## 9) Audit & Rollback

- Check audit log path from `logging.audit.file`
- Ensure `/sys` operations emit JSONL events
- Validate rollback command:
  - `kapy sys config rollback <path> <backup_path>`

Expected:
- every system op has audit entry
- rollback restores target content from backup

## 10) Canary Window

Observe for 24h:
- restart count
- latency and failures
- audit anomaly review

Suggested checks:

```bash
journalctl -u cli-gateway-session@bot-a -n 200 --no-pager
journalctl -u cli-gateway-system@ops-a -n 200 --no-pager
journalctl -u cli-gateway-sys-executor@ops-a -n 200 --no-pager
```

## 11) Rollback

```bash
sudo systemctl disable --now cli-gateway-system@ops-a
sudo systemctl disable --now cli-gateway-session@bot-a
sudo systemctl disable --now cli-gateway-sys-executor@ops-a
# deploy previous release
sudo systemctl daemon-reload
sudo systemctl enable --now cli-gateway-session@bot-a
```
