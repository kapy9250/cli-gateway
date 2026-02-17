# systemd Deployment Templates

This directory provides templates for split-plane deployment:

- `cli-gateway-session@.service`: non-root user-level mode (`--mode session`)
- `cli-gateway-system@.service`: non-root ops gateway (`--mode system`, chat + 2FA)
- `cli-gateway-sys-executor@.service`: root privileged executor (`system_service_main.py`)

Config scope:
- `cli-gateway-system@.service` needs a full gateway config (`session`, `agents`, `channels`, auth/2FA as needed).
- `cli-gateway-sys-executor@.service` can run with a minimal privileged config (`system_service` + `system_ops` + logging).

Both templates load:

- config: `/etc/cli-gateway/%i.yaml`
- env: `/etc/cli-gateway/%i.env` (optional)
- runtime flags: `--instance-id %i --namespace-paths`
- default `CODEX_HOME`: `/opt/cli-gateway/data/codex-home-%i` (override in `%i.env` if needed)

Python runtime behavior:
- units prefer `/opt/cli-gateway/.venv/bin/python3`
- if the venv is missing, they fall back to system `python3`

Recommended bootstrap:

```bash
cd /opt/cli-gateway
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

## Bootstrap Ops Config

Generate a full `%i.yaml` for system mode from an existing gateway config:

```bash
cd /opt/cli-gateway
./.venv/bin/python scripts/bootstrap_ops_config.py \
  --source-config /data/workspaces/cli-gateway/config.yaml \
  --privileged-config /etc/cli-gateway/ops-a.yaml \
  --output /etc/cli-gateway/ops-a.yaml \
  --instance-id ops-a \
  --health-port 18810 \
  --channel-profile telegram-only \
  --print-otpauth
```

Notes:
- Use a dedicated `health.port` per instance to avoid bind conflicts.
- `telegram-only` is recommended for system ops instances to reduce attack surface.
- If `cli-gateway.service` (legacy single-instance unit) is present, disable it to avoid token conflicts.
- For Codex CLI auth, copy/prepare `auth.json` under the configured `CODEX_HOME` and ensure it is readable by the service user.

## Install

```bash
sudo install -m 0644 systemd/cli-gateway-session@.service /etc/systemd/system/
sudo install -m 0644 systemd/cli-gateway-system@.service /etc/systemd/system/
sudo install -m 0644 systemd/cli-gateway-sys-executor@.service /etc/systemd/system/
sudo systemctl daemon-reload
```

## Start Session Instance

```bash
sudo systemctl enable --now cli-gateway-session@bot-a
sudo systemctl status cli-gateway-session@bot-a --no-pager -l
```

## Start System-Admin Instance

```bash
sudo systemctl enable --now cli-gateway-system@ops-a
sudo systemctl status cli-gateway-system@ops-a --no-pager -l
```

## Start Privileged Executor

```bash
sudo systemctl enable --now cli-gateway-sys-executor@ops-a
sudo systemctl status cli-gateway-sys-executor@ops-a --no-pager -l
```

## Stop / Disable

```bash
sudo systemctl disable --now cli-gateway-session@bot-a
sudo systemctl disable --now cli-gateway-system@ops-a
sudo systemctl disable --now cli-gateway-sys-executor@ops-a
```

## Verify Unit Syntax

```bash
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-session@.service
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-system@.service
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-sys-executor@.service
```
