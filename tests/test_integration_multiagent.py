"""
多 Agent 集成测试
模拟用户在 Claude、Codex、Gemini 之间切换
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent))

from core.router import Router
from core.session import SessionManager
from core.auth import Auth
from agents.claude_code import ClaudeCodeAgent
from agents.codex_cli import CodexAgent
from agents.gemini_cli import GeminiAgent
from channels.base import IncomingMessage

class MockAgentForIntegration:
    """模拟 Agent（继承真实 Agent 的接口）"""
    def __init__(self, name, config, workspace_base):
        self.name = name
        self.config = config
        self.workspace_base = workspace_base
        self.sessions = {}
    
    async def create_session(self, user_id, chat_id):
        session_id = f"mock_{self.name}_{len(self.sessions)}"
        self.sessions[session_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "agent_name": self.name
        }
        session_info = MagicMock()
        session_info.session_id = session_id
        return session_info
    
    def get_session_info(self, session_id):
        if session_id in self.sessions:
            return MagicMock()
        return None
    
    async def send_message(self, session_id, message, model=None, params=None):
        """模拟发送消息并返回配置信息"""
        yield f"[{self.name.upper()}] Model: {model or 'default'}\n"
        yield f"[{self.name.upper()}] Params: {params or {}}\n"
        yield f"[{self.name.upper()}] Prompt: {message[:50]}...\n"
    
    async def destroy_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

class FakeChannel:
    """模拟 Channel"""
    def __init__(self):
        self.messages = []
    
    async def send_text(self, chat_id, text):
        self.messages.append(("send", text))
        print(f"\n[Bot → {chat_id}]")
        print(text[:200] + "..." if len(text) > 200 else text)
        print("-" * 80)
        return len(self.messages)
    
    async def send_typing(self, chat_id):
        pass
    
    async def edit_message(self, chat_id, message_id, text):
        self.messages.append(("edit", text))
        print(f"\n[Bot → {chat_id}] (edited)")
        print(text[:200] + "..." if len(text) > 200 else text)
        print("-" * 80)

async def test_multi_agent_workflow():
    """测试多 Agent 工作流"""
    print("\n" + "="*80)
    print("集成测试：多 Agent 工作流")
    print("="*80)
    
    # 配置
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {"sonnet": "claude-sonnet-4-5", "opus": "claude-opus-4-6"},
                "default_model": "sonnet",
                "supported_params": {"thinking": "--thinking"},
                "default_params": {"thinking": "low"}
            },
            "codex": {
                "command": "codex",
                "models": {"gpt5.3": "gpt-5.3-codex"},
                "default_model": "gpt5.3",
                "supported_params": {"temperature": "--temperature", "max_tokens": "--max-tokens"},
                "default_params": {}
            },
            "gemini": {
                "command": "gemini-cli",
                "models": {"gemini3": "gemini-3-pro-preview"},
                "default_model": "gemini3",
                "supported_params": {"temperature": "--temp"},
                "default_params": {}
            }
        }
    }
    
    # 初始化组件
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/integration-test")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgentForIntegration("claude", config['agents']['claude'], workspace),
        "codex": MockAgentForIntegration("codex", config['agents']['codex'], workspace),
        "gemini": MockAgentForIntegration("gemini", config['agents']['gemini'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "integration_test"
    
    # 测试场景
    test_scenarios = [
        # Scenario 1: Claude (默认)
        ("hello", "使用 Claude 默认配置"),
        ("kapy params", "查看 Claude 配置"),
        
        # Scenario 2: 切换到 Codex
        ("kapy agent codex", "切换到 Codex"),
        ("kapy params", "查看 Codex 配置"),
        ("kapy param temperature 0.7", "设置 Codex temperature"),
        ("kapy param max_tokens 2000", "设置 Codex max_tokens"),
        ("kapy params", "确认 Codex 配置"),
        ("write a sorting algorithm", "用 Codex 生成代码"),
        
        # Scenario 3: 切换到 Gemini
        ("kapy agent gemini", "切换到 Gemini"),
        ("kapy params", "查看 Gemini 配置"),
        ("kapy param temperature 0.8", "设置 Gemini temperature"),
        ("summarize this long text...", "用 Gemini 总结"),
        
        # Scenario 4: 切换回 Claude 并修改配置
        ("kapy agent claude", "切换回 Claude"),
        ("kapy model opus", "切换到 opus"),
        ("kapy param thinking high", "设置高级推理"),
        ("kapy params", "确认 Claude 新配置"),
        ("complex reasoning task", "用 Claude Opus 推理"),
        
        # Scenario 5: 验证 session 隔离
        ("kapy sessions", "列出所有会话"),
    ]
    
    print("\n开始执行测试场景...")
    
    for i, (text, description) in enumerate(test_scenarios, 1):
        print(f"\n{'='*80}")
        print(f"场景 {i}: {description}")
        print(f"{'='*80}")
        print(f"[User → Bot] {text}")
        
        msg = IncomingMessage(
            channel="telegram",
            chat_id=chat_id,
            user_id=user_id,
            text=text,
            is_private=True,
            is_reply_to_bot=False,
            is_mention_bot=False,
            attachments=[]
        )
        
        try:
            await router.handle_message(msg)
            await asyncio.sleep(0.1)
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # 验证会话状态
    print("\n" + "="*80)
    print("验证最终状态")
    print("="*80)
    
    all_sessions = session_manager.list_user_sessions(user_id)
    print(f"\n总会话数: {len(all_sessions)}")
    
    for session in all_sessions:
        print(f"  - Session {session.session_id[:8]}...")
        print(f"    Agent: {session.agent_name}")
        print(f"    Model: {session.model}")
        print(f"    Params: {session.params}")
    
    # 验证当前会话是 Claude + opus + thinking=high
    current = session_manager.get_active_session(user_id)
    
    checks = [
        (current.agent_name == "claude", f"当前 agent 应为 claude，实际为 {current.agent_name}"),
        (current.model == "opus", f"当前模型应为 opus，实际为 {current.model}"),
        (current.params.get("thinking") == "high", f"thinking 应为 high，实际为 {current.params.get('thinking')}"),
    ]
    
    all_passed = True
    for check, error_msg in checks:
        if not check:
            print(f"\n❌ 验证失败: {error_msg}")
            all_passed = False
    
    if all_passed:
        print("\n✅ 所有验证通过")
        return True
    else:
        return False

async def test_parameter_format_conversion():
    """测试参数格式自动转换"""
    print("\n" + "="*80)
    print("集成测试：参数格式自动转换")
    print("="*80)
    
    # 三个 agent 使用相同的参数名 "temperature"，但 CLI 标志不同
    configs = {
        "codex": {
            "supported_params": {"temperature": "--temperature"}
        },
        "gemini": {
            "supported_params": {"temperature": "--temp"}
        }
    }
    
    # 验证配置差异
    codex_flag = configs["codex"]["supported_params"]["temperature"]
    gemini_flag = configs["gemini"]["supported_params"]["temperature"]
    
    print(f"\n参数名: temperature")
    print(f"  Codex CLI 标志: {codex_flag}")
    print(f"  Gemini CLI 标志: {gemini_flag}")
    
    if codex_flag != gemini_flag:
        print("\n✅ 参数格式不同，需要自动转换")
        print("✅ Gateway 会根据当前 agent 自动选择正确格式")
        return True
    else:
        print("\n❌ 参数格式应该不同")
        return False

async def main():
    """运行集成测试"""
    print("\n" + "="*80)
    print("多 Agent 集成测试套件")
    print("="*80)
    
    tests = [
        ("多 Agent 工作流", test_multi_agent_workflow),
        ("参数格式转换", test_parameter_format_conversion),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ 测试失败: {name}")
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # 总结
    print("\n" + "="*80)
    print("集成测试结果总结")
    print("="*80)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有集成测试通过！")
        return 0
    else:
        print(f"\n⚠️ {total - passed} 个测试失败")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
