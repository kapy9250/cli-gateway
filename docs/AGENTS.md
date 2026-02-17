# Agent 配置指南

CLI Gateway 支持多种 CLI 工具作为 Agent。每个 Agent 都有独立的模型和参数配置。

---

## 支持的 Agents

| Agent | 状态 | CLI 工具 | 默认模型 |
|-------|------|---------|---------|
| **claude** | ✅ 启用 | Claude Code | sonnet |
| **codex** | 🔧 可用 | GPT Codex | gpt5.3 |
| **gemini** | 🔧 可用 | Gemini CLI | gemini3 |

---

## Claude Code

### 配置示例

```yaml
agents:
  claude:
    enabled: true
    display_name: "Claude Code"
    command: "claude"
    args_template: ["-p", "{prompt}", "--session-id", "{session_id}", "--output-format", "text"]
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

### 支持的模型

| 别名 | 完整名称 | 特点 |
|------|---------|------|
| `sonnet` | claude-sonnet-4-5 | 平衡性能和速度 |
| `opus` | claude-opus-4-6 | 最强推理能力 |
| `haiku` | claude-haiku-4-5 | 最快响应 |

### 支持的参数

| 参数 | CLI 标志 | 说明 | 示例 |
|------|---------|------|------|
| `thinking` | `--thinking` | 推理模式 | low, medium, high |
| `max_turns` | `--max-turns` | 最大轮数 | 3, 5, 10 |

### 使用示例

```
kapy model opus
kapy param thinking high
kapy param max_turns 5
```

---

## Codex CLI

### 配置示例

```yaml
agents:
  codex:
    enabled: true  # 设置为 true 启用
    display_name: "GPT Codex"
    command: "codex"
    args_template: ["--prompt", "{prompt}", "--session", "{session_id}"]
    models:
      gpt5.3: "gpt-5.3-codex"
    default_model: "gpt5.3"
    supported_params:
      model: "--model"
      temperature: "--temperature"
      max_tokens: "--max-tokens"
    default_params: {}
```

### 支持的模型

| 别名 | 完整名称 | 特点 |
|------|---------|------|
| `gpt5.3` | gpt-5.3-codex | 最新 GPT 编程模型 |

### 支持的参数

| 参数 | CLI 标志 | 说明 | 示例 |
|------|---------|------|------|
| `temperature` | `--temperature` | 随机性 (0-2) | 0.7, 1.0, 1.5 |
| `max_tokens` | `--max-tokens` | 最大输出 token | 1000, 2000 |

### 使用示例

```
kapy agent codex
kapy param temperature 0.7
kapy param max_tokens 2000
```

---

## Gemini CLI

### 配置示例

```yaml
agents:
  gemini:
    enabled: true  # 设置为 true 启用
    display_name: "Gemini CLI"
    command: "gemini-cli"
    args_template: ["-p", "{prompt}"]
    models:
      gemini3: "gemini-3-pro-preview"
    default_model: "gemini3"
    supported_params:
      model: "-m"
      temperature: "--temp"
    default_params: {}
```

### 支持的模型

| 别名 | 完整名称 | 特点 |
|------|---------|------|
| `gemini3` | gemini-3-pro-preview | 2M token 上下文 |

### 支持的参数

| 参数 | CLI 标志 | 说明 | 示例 |
|------|---------|------|------|
| `temperature` | `--temp` | 随机性 (0-1) | 0.5, 0.8 |

### 使用示例

```
kapy agent gemini
kapy param temperature 0.8
```

---

## 参数格式对比

不同 Agent 使用不同的 CLI 参数格式：

| Agent | 模型标志 | 温度标志 | 示例命令 |
|-------|---------|---------|---------|
| Claude | `--model` | `--thinking` | `claude --model opus --thinking high` |
| Codex | `--model` | `--temperature` | `codex --model gpt5.3 --temperature 0.7` |
| Gemini | `-m` | `--temp` | `gemini-cli -m gemini3 --temp 0.8` |

**Gateway 自动处理这些差异** ✅

---

## 添加新 Agent

### 1. 创建 Agent 类

```python
# agents/my_cli.py
from agents.base import BaseAgent

class MyAgent(BaseAgent):
    async def create_session(self, user_id: str, chat_id: str):
        # 创建会话逻辑
        pass
    
    async def send_message(self, session_id: str, message: str, model=None, params=None):
        # 发送消息逻辑
        pass
```

### 2. 配置 Agent

```yaml
agents:
  myagent:
    enabled: true
    command: "my-cli"
    models:
      default: "model-name"
    supported_params:
      param1: "--param1"
```

### 3. 注册 Agent

```python
# main.py
from agents.my_cli import MyAgent

if config['agents'].get('myagent', {}).get('enabled', False):
    agents['myagent'] = MyAgent(
        name='myagent',
        config=config['agents']['myagent'],
        workspace_base=workspace_base
    )
```

---

## 故障排查

### Agent 未找到

```
❌ 未找到 agent: codex
```

**解决方案：**
1. 检查 `config.yaml` 中 `enabled: true`
2. 确认 CLI 工具已安装：`which codex`
3. 重启 Gateway

### 参数不支持

```
❌ claude 不支持参数 temperature
```

**解决方案：**
查看该 Agent 的 `supported_params`，使用正确的参数名。

### 命令执行失败

```
❌ Codex CLI 未安装或未找到命令: codex
```

**解决方案：**
1. 安装对应的 CLI 工具
2. 确保在 PATH 中
3. 检查权限

---

## 性能对比

| Agent | 速度 | 上下文 | 成本 | 适用场景 |
|-------|------|--------|------|---------|
| **Claude Sonnet** | ⚡⚡⚡ | 200K | 💰💰 | 日常编程 |
| **Claude Opus** | ⚡⚡ | 200K | 💰💰💰 | 复杂推理 |
| **Claude Haiku** | ⚡⚡⚡⚡ | 200K | 💰 | 快速回答 |
| **GPT-5.3 Codex** | ⚡⚡⚡ | 128K | 💰💰💰 | 高级编程 |
| **Gemini 3** | ⚡⚡⚡ | 2M | 💰 | 长文本 |

---

**文档版本：** Phase 3  
**最后更新：** 2026-02-07
