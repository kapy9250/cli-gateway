# CLI Gateway

**通用 CLI 代理网关** - 通过 Telegram 访问 Claude Code、Codex、Gemini 等 CLI 工具

[![Tests](https://img.shields.io/badge/tests-6%2F6%20passing-brightgreen)](TEST_REPORT.md)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org)

---

## ✨ 特性

- 🤖 **多 Agent 支持** - Claude Code、Codex、Gemini（架构完成，待启用）
- 🔄 **动态模型切换** - sonnet/opus/haiku，随时切换
- ⚙️ **参数配置** - thinking、max_turns 等参数动态调整
- 💾 **会话持久化** - 重启后自动恢复会话
- 📡 **流式输出** - 实时显示 agent 响应
- 📎 **附件支持** - 发送图片、文档给 agent
- 🎯 **两种命令格式** - 支持 `/model` 和 `kapybara model` 两种格式

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
- 添加你的 Telegram user ID
- 配置 Claude Code CLI 路径

### 3. 运行

```bash
python main.py
```

---

## 📖 使用指南

### 命令格式

**两种格式都支持：**

| 传统格式 | 新格式（推荐） | 说明 |
|----------|---------------|------|
| `/help` | `kapybara help` | 显示帮助 |
| `/model opus` | `kapybara model opus` | 切换模型 |
| `/param thinking high` | `kapybara param thinking high` | 设置参数 |
| `/params` | `kapybara params` | 查看配置 |

**新格式的优势：**
- 不与 Telegram 的 `/` 命令冲突
- 更像 CLI 工具的使用方式
- 支持更复杂的参数组合

---

### 会话管理

```bash
kapybara agent claude      # 切换到 Claude Code
kapybara sessions          # 列出所有会话
kapybara current           # 查看当前会话
kapybara switch <id>       # 切换会话
kapybara kill              # 销毁当前会话
```

### 模型配置

```bash
kapybara model             # 列出可用模型
kapybara model opus        # 切换到 opus
kapybara model sonnet      # 切换到 sonnet
kapybara model haiku       # 切换到 haiku
```

### 参数配置

```bash
kapybara param             # 列出可用参数
kapybara param thinking high    # 设置 thinking 模式
kapybara param max_turns 5      # 设置最大轮数
kapybara params            # 查看当前配置
kapybara reset             # 重置为默认配置
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
┌─────────────────┐
│  Telegram Bot   │
└────────┬────────┘
         │
    ┌────▼─────┐
    │  Router  │
    └────┬─────┘
         │
    ┌────▼─────────┐
    │ Session Mgr  │
    └────┬─────────┘
         │
    ┌────▼─────┐
    │  Agents  │
    │          │
    │ - Claude │
    │ - Codex  │
    │ - Gemini │
    └──────────┘
```

**核心组件：**
- **Router** - 命令路由和消息转发
- **SessionManager** - 会话管理和持久化
- **Agent** - CLI 工具适配器（Claude Code, Codex, Gemini）
- **Channel** - 消息平台适配器（Telegram）

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
python tests/manual_test_bot.py
```

**测试覆盖：**
- ✅ 基础命令
- ✅ 模型切换
- ✅ 参数配置
- ✅ 消息发送
- ✅ 会话持久化
- ✅ Kapybara 新格式

**测试结果：6/6 通过** 🎉

详细报告：[TEST_REPORT.md](TEST_REPORT.md)

---

## 📋 TODO

**Phase 3: 多 CLI 集成**
- [ ] 启用 Codex CLI
- [ ] 启用 Gemini CLI
- [ ] 测试不同 CLI 的参数格式

**功能增强**
- [ ] 错误重试机制
- [ ] 日志结构化
- [ ] 健康检查端点
- [ ] 多用户并发测试

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
- 会话保存在 `workspaces/.sessions.json`
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

**Made with ❤️ by Kapybara 🦫**
