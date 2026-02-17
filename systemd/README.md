# systemd Deployment Templates

This directory provides instance templates for two security levels:

- `cli-gateway-session@.service`: non-root user-level mode (`--mode session`)
- `cli-gateway-system@.service`: privileged system-admin mode (`--mode system`)

Both templates load:

- config: `/etc/cli-gateway/%i.yaml`
- env: `/etc/cli-gateway/%i.env` (optional)
- runtime flags: `--instance-id %i --namespace-paths`

## Install

```bash
sudo install -m 0644 systemd/cli-gateway-session@.service /etc/systemd/system/
sudo install -m 0644 systemd/cli-gateway-system@.service /etc/systemd/system/
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

## Stop / Disable

```bash
sudo systemctl disable --now cli-gateway-session@bot-a
sudo systemctl disable --now cli-gateway-system@ops-a
```

## Verify Unit Syntax

```bash
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-session@.service
sudo systemd-analyze verify /etc/systemd/system/cli-gateway-system@.service
```
