# CLI Gateway

**通用 CLI 代理网关** - 通过 Telegram / Discord / Email 访问 Claude Code、Codex、Gemini 等 CLI 工具

[![Tests](https://img.shields.io/badge/tests-pytest-blue)](#-测试)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)

---

## ✨ 特性

- 🤖 **多 Agent 支持** - Claude Code（已启用）、Codex、Gemini（可选启用）
- 🔄 **动态模型切换** - sonnet/opus/haiku/gpt5.3/gemini3，随时切换
- ⚙️ **参数配置** - thinking、temperature、max_turns 等参数动态调整
- 💾 **会话持久化** - 重启后自动恢复会话
- 🧠 **长期记忆系统（可选）** - PostgreSQL + pgvector，短/中/长三级记忆与知识树检索
- 📡 **流式输出** - 实时显示 agent 响应
- 📎 **附件支持** - 发送图片、文档给 agent
- 🎯 **两种命令格式** - 支持 `/model` 和 `kapy model` 两种格式
- 🧩 **多实例运行** - 支持 `--config` / `--instance-id` 同目录多实例部署
- 🔐 **双权限级别** - `session`（普通会话）与 `system`（系统运维）双模式
- 🔒 **2FA + 审计** - 敏感读写/运维操作支持挑战审批与 JSONL 审计日志
- 🔌 **可扩展架构** - 轻松添加新的 CLI 工具

---

## 🚀 快速开始

### 1. 安装依赖

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

编辑 `config.yaml`：
- 设置 Telegram bot token
- 如使用 Discord，设置 Discord bot token（可选 `allow_bots: true/false`，默认 `true`）
- 添加你的 Telegram user ID
- 配置 Claude Code CLI 路径

### 3. 运行

```bash
python main.py
```

多实例示例（同一代码目录，不同配置和实例 ID）：

```bash
python main.py --config /etc/cli-gateway/bot-a.yaml --mode session --instance-id bot-a
python main.py --config /etc/cli-gateway/bot-b.yaml --mode session --instance-id bot-b --health-port 18801
```

仅验证配置解析（不启动机器人）：

```bash
python main.py --config config.yaml --instance-id test-a --validate-only
python main.py --config config.yaml --instance-id test-b --namespace-paths --validate-only
```

system 模式下可用 2FA 审批命令（需配置 `two_factor` 与 `system_admin_users`）：

```bash
kapy sysauth plan restart nginx
kapy sysauth approve <challenge_id> <totp_code>
kapy sysauth status <challenge_id>
kapy sysauth setup start
kapy sysauth setup verify <totp_code>
```

system 模式运维命令（首次敏感操作触发 2FA，直接回复验证码；同一聊天 10 分钟内免挑战）：

```bash
kapy sys journal cli-gateway.service 80
kapy sys read /etc/hosts
kapy sys read /etc/shadow
kapy sys cron list
kapy sys cron upsert backup-job "*/5 * * * *" "/usr/local/bin/backup.sh"
kapy sys docker ps -a
kapy sys config write /etc/myapp.conf <base64_content>
kapy sys config rollback /etc/myapp.conf /etc/myapp.conf.bak.20260216_200000
# 可选兼容：仍支持 --challenge <challenge_id>
```

root 侧 system service（独立进程）：

```bash
python system_service_main.py --config /etc/cli-gateway/ops-a.yaml --validate-only
python system_service_main.py --config /etc/cli-gateway/ops-a.yaml
```

使用 `systemd` 模板部署时，会优先使用 `/opt/cli-gateway/.venv/bin/python3`（不存在时回退系统 `python3`）。
建议先在部署目录初始化依赖：

```bash
cd /opt/cli-gateway
python3 -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

建议在每次部署后写入版本文件（供 `/current` 展示）：

```bash
cd /opt/cli-gateway
./.venv/bin/python scripts/write_runtime_version.py
# 会写入 /opt/cli-gateway/.runtime-version，例如 git:7ef7313
```

`cli-gateway-system@<id>` 仍是完整网关进程，需要 `<id>.yaml` 提供 `session/agents/channels` 等配置。
如果只验证 root 执行桥接，可仅启动 `cli-gateway-sys-executor@<id>`（最小 `system_service/system_ops` 配置即可）。

可使用脚本从现有配置自动生成 ops 配置：

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

建议为每个实例使用独立 `health.port`，并确保同一 Telegram token 只由一个运行实例使用。
若系统中仍有 legacy `cli-gateway.service`，建议停用以避免重复拉起 bot：

```bash
sudo systemctl disable --now cli-gateway.service
```

建议在 `system_service.allowed_peer_uids` 中限制可调用该 socket 的本地 UID（通常是 `cli-gateway` 用户）。
默认建议开启 `system_service.enforce_peer_uid_allowlist=true`，避免任意本地 UID 访问 root 执行器。
建议同时开启 `system_service.enforce_peer_unit_allowlist=true`，并配置
`system_service.allowed_peer_units=["cli-gateway-system@<id>.service"]`，
将 root 执行器绑定到预期 system 实例。
建议开启 `system_service.require_grant_for_all_ops=true`，确保所有 `/sys` 操作都经过 2FA->grant 流程。
并配置 `system_service.socket_parent_mode/socket_mode/socket_uid/socket_gid`，确保目录与 Unix socket 权限最小化且可被目标网关进程访问。

所有 `/sys` 操作会写入审计日志（`logging.audit.file`，JSONL）。
审计日志默认会对 `text/output/stderr/stdout` 做脱敏，仅记录摘要元数据。
灰度与上线步骤见：`docs/OPERATIONS_ROLLOUT.md`

---

## 📖 使用指南

### 命令格式

**两种格式都支持：**

| 传统格式 | 新格式（推荐） | 说明 |
|----------|---------------|------|
| `/help` | `kapy help` | 显示帮助 |
| `/model opus` | `kapy model opus` | 切换模型 |
| `/param thinking high` | `kapy param thinking high` | 设置参数 |
| `/params` | `kapy params` | 查看配置 |

**新格式的优势：**
- 不与 Telegram 的 `/` 命令冲突
- 更像 CLI 工具的使用方式
- 支持更复杂的参数组合

---

### 会话管理

```bash
kapy agent claude      # 切换到 Claude Code
kapy sessions          # 列出所有会话
kapy current           # 查看当前会话
kapy switch <id>       # 切换会话
kapy kill              # 销毁当前会话
```

### 模型配置

```bash
kapy model             # 列出可用模型
kapy model opus        # 切换到 opus
kapy model sonnet      # 切换到 sonnet
kapy model haiku       # 切换到 haiku
```

### 参数配置

```bash
kapy param             # 列出可用参数
kapy param thinking high    # 设置 thinking 模式
kapy param max_turns 5      # 设置最大轮数
kapy params            # 查看当前配置
kapy reset             # 重置为默认配置
```

### 记忆管理（可选）

```bash
kapy memory                  # 查看记忆系统状态
kapy memory list short 20    # 列出短期记忆
kapy memory find 部署流程     # 检索记忆
kapy memory note nginx重启步骤
kapy memory pin 12
```

### 发送消息

直接发送文本即可：
```
写一个 Python 函数计算斐波那契数列
```

发送附件：
- 直接发送图片/文档
- Agent 会收到文件路径

---

## 🏗️ 架构

```
┌──────────────────────────────┐
│ session/ops gateway (非 root)│
│ - 白名单鉴权                 │
│ - 2FA challenge 交互         │
│ - /sys 指令编排              │
└───────────────┬──────────────┘
                │ Unix Socket + grant token
┌───────────────▼──────────────┐
│ privileged system service     │
│ - root 执行器                │
│ - 验签一次性授权票据          │
│ - 结构化 action 执行          │
└──────────────────────────────┘
```

**核心组件：**
- **Router** - 命令路由和消息转发
- **SessionManager** - 会话管理和持久化
- **Agent** - CLI 工具适配器（Claude Code, Codex, Gemini）
- **Channel** - 消息平台适配器（Telegram / Discord / Email）
- **SystemServiceClient** - `/sys` 指令到 root 服务的本地桥接
- **SystemGrantManager** - 2FA 后签发短时一次性授权票据

---

## ⚙️ 配置说明

### Agent 配置

```yaml
agents:
  claude:
    enabled: true
    command: "claude"
    models:
      sonnet: "claude-sonnet-4-5"
      opus: "claude-opus-4-6"
      haiku: "claude-haiku-4-5"
    default_model: "sonnet"
    supported_params:
      model: "--model"
      thinking: "--thinking"
      max_turns: "--max-turns"
    default_params:
      thinking: "low"
```

**字段说明：**
- `models` - 模型别名映射
- `default_model` - 默认模型
- `supported_params` - 支持的参数及其命令行标志
- `default_params` - 默认参数值

### 添加新 Agent

1. 在 `config.yaml` 中定义 agent
2. 创建 Agent 类（继承 `BaseAgent`）
3. 在 `main.py` 中注册

---

## 🧪 测试

### 运行测试套件

```bash
pytest -q
```

手动联调（可选）：

```bash
python tests/manual_test_bot.py
```

建议在修改 system 权限、2FA、`/sys` 指令相关代码后，至少执行：
- `tests/test_auth.py`
- `tests/test_system_mode_security.py`
- `tests/test_system_executor_security.py`
- `tests/test_system_grant.py`
- `tests/test_system_service_bridge.py`
- `tests/test_sys_command_remote_bridge.py`

---

## 📋 Roadmap

已完成：
- [x] 多实例配置与启动参数（`--config` / `--instance-id` / `--mode`）
- [x] systemd 模板化部署（session / system 双模板）
- [x] system_admin 身份分离与 mode 门禁
- [x] `/sysauth` 2FA 挑战/审批流（TOTP）
- [x] `/sys` 日志/文件/cron/docker/config 运维指令
- [x] 系统运维审计日志与配置回滚

进行中：
- [ ] 生产环境 canary 观察与告警阈值固化
- [ ] system 模式运维命令的端到端集成测试

---

## 🐛 问题排查

### Bot 无响应
1. 检查 bot token 是否正确
2. 确认你的 user ID 在 `allowed_users` 中
3. 查看日志：`tail -f logs/gateway.log`

### Claude Code 命令失败
1. 确认 `claude` 命令在 PATH 中：`which claude`
2. 检查 workspace 权限
3. 查看错误日志

### 会话丢失
- 会话保存在 `workspaces/<instance_id>/.sessions.json`
- 检查文件权限
- 查看 SessionManager 日志

---

## 📄 License

MIT

---

## 🙏 致谢

- [Claude Code](https://code.claude.com) - Anthropic's CLI coding assistant
- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot) - Telegram Bot API wrapper

---

**Maintained by CLI Gateway contributors**
