"""
手动测试脚本：模拟 Telegram 用户交互
不需要真实 Telegram/Claude Code，完全本地测试
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.router import Router
from core.session import SessionManager
from core.auth import Auth
from agents.claude_code import ClaudeCodeAgent
from channels.base import IncomingMessage

class FakeChannel:
    """模拟 Telegram Channel"""
    async def send_text(self, chat_id, text):
        print(f"\n[Bot → User {chat_id}]")
        print(text)
        print("-" * 80)
        return 1  # Mock message_id
    
    async def send_typing(self, chat_id):
        print(f"[Bot typing...]")
    
    async def edit_message(self, chat_id, message_id, text):
        print(f"\n[Bot → User {chat_id}] (edited msg {message_id})")
        print(text[:200] + "..." if len(text) > 200 else text)
        print("-" * 80)

class MockAgent:
    """模拟 Claude Code Agent（不调用真实 CLI）"""
    def __init__(self, name, config, workspace_base):
        self.name = name
        self.config = config
        self.workspace_base = workspace_base
        self.sessions = {}
    
    async def create_session(self, user_id, chat_id, session_id=None, work_dir=None, scope_dir=None):
        session_id = str(session_id or f"mock_{len(self.sessions)}")
        if work_dir is None:
            base_dir = self.workspace_base / str(scope_dir) if scope_dir else self.workspace_base
            work_dir = base_dir / f"sess_{session_id}"
        work_dir.mkdir(parents=True, exist_ok=True)
        self.sessions[session_id] = {
            "user_id": user_id,
            "chat_id": chat_id,
            "created": True,
            "work_dir": str(work_dir),
        }
        return MagicMock(session_id=session_id, work_dir=work_dir)
    
    def get_session_info(self, session_id):
        if session_id in self.sessions:
            return MagicMock()
        return None
    
    async def send_message(self, session_id, message, model=None, params=None, run_as_root=False):
        """模拟返回"""
        print(f"\n[MockAgent] Executing with:")
        print(f"  Session: {session_id}")
        print(f"  Model: {model}")
        print(f"  Params: {params}")
        print(f"  Prompt: {message[:100]}...")
        
        # Simulate streaming output
        yield "[Mock Response Line 1]\n"
        await asyncio.sleep(0.1)
        yield "[Mock Response Line 2]\n"
        await asyncio.sleep(0.1)
        yield f"[Mock: Executed with model={model}, params={params}]"
    
    async def destroy_session(self, session_id):
        if session_id in self.sessions:
            del self.sessions[session_id]

async def test_basic_commands():
    """测试基础命令"""
    print("\n" + "="*80)
    print("TEST 1: 基础命令测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {
                    "sonnet": "claude-sonnet-4-5",
                    "opus": "claude-opus-4-6",
                    "haiku": "claude-haiku-4-5"
                },
                "default_model": "sonnet",
                "supported_params": {
                    "thinking": "--thinking",
                    "max_turns": "--max-turns"
                },
                "default_params": {
                    "thinking": "low"
                }
            }
        }
    }
    
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/cli-gateway-test")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgent("claude", config['agents']['claude'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "test_chat"
    
    test_messages = [
        ("/start", "启动测试"),
        ("/help", "帮助命令"),
        ("/params", "查看默认配置"),
        ("/model", "列出可用模型"),
        ("/param", "列出可用参数"),
    ]
    
    for text, description in test_messages:
        print(f"\n[User → Bot] {description}")
        print(f"> {text}")
        print("=" * 80)
        
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
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    print("\n✅ TEST 1 PASSED")
    return True

async def test_model_switching():
    """测试模型切换"""
    print("\n" + "="*80)
    print("TEST 2: 模型切换测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {
                    "sonnet": "claude-sonnet-4-5",
                    "opus": "claude-opus-4-6",
                    "haiku": "claude-haiku-4-5"
                },
                "default_model": "sonnet",
                "supported_params": {
                    "thinking": "--thinking"
                },
                "default_params": {
                    "thinking": "low"
                }
            }
        }
    }
    
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/cli-gateway-test2")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgent("claude", config['agents']['claude'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "test_chat"
    
    # Create session first
    msg = IncomingMessage(
        channel="telegram", chat_id=chat_id, user_id=user_id,
        text="hello", is_private=True, is_reply_to_bot=False,
        is_mention_bot=False, attachments=[]
    )
    await router.handle_message(msg)
    
    # Test model switching
    test_sequence = [
        ("/params", "查看初始配置"),
        ("/model opus", "切换到 opus"),
        ("/params", "确认 opus"),
        ("/model haiku", "切换到 haiku"),
        ("/params", "确认 haiku"),
        ("/model sonnet", "切换回 sonnet"),
    ]
    
    for text, description in test_sequence:
        print(f"\n[User → Bot] {description}")
        print(f"> {text}")
        print("=" * 80)
        
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
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Verify final state
    session = session_manager.get_active_session(user_id)
    if session.model != "sonnet":
        print(f"❌ Expected model=sonnet, got {session.model}")
        return False
    
    print("\n✅ TEST 2 PASSED")
    return True

async def test_param_configuration():
    """测试参数配置"""
    print("\n" + "="*80)
    print("TEST 3: 参数配置测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {"sonnet": "claude-sonnet-4-5"},
                "default_model": "sonnet",
                "supported_params": {
                    "thinking": "--thinking",
                    "max_turns": "--max-turns"
                },
                "default_params": {
                    "thinking": "low"
                }
            }
        }
    }
    
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/cli-gateway-test3")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgent("claude", config['agents']['claude'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "test_chat"
    
    # Create session
    msg = IncomingMessage(
        channel="telegram", chat_id=chat_id, user_id=user_id,
        text="init", is_private=True, is_reply_to_bot=False,
        is_mention_bot=False, attachments=[]
    )
    await router.handle_message(msg)
    
    test_sequence = [
        ("/param thinking high", "设置 thinking=high"),
        ("/params", "查看配置"),
        ("/param max_turns 5", "设置 max_turns=5"),
        ("/params", "查看配置"),
        ("/reset", "重置配置"),
        ("/params", "查看重置后配置"),
    ]
    
    for text, description in test_sequence:
        print(f"\n[User → Bot] {description}")
        print(f"> {text}")
        print("=" * 80)
        
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
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    # Verify reset worked
    session = session_manager.get_active_session(user_id)
    if session.params.get("thinking") != "low":
        print(f"❌ Expected thinking=low after reset, got {session.params.get('thinking')}")
        return False
    
    print("\n✅ TEST 3 PASSED")
    return True

async def test_message_with_config():
    """测试带配置的消息发送"""
    print("\n" + "="*80)
    print("TEST 4: 带配置的消息测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {"opus": "claude-opus-4-6"},
                "default_model": "opus",
                "supported_params": {
                    "thinking": "--thinking"
                },
                "default_params": {
                    "thinking": "high"
                }
            }
        }
    }
    
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/cli-gateway-test4")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgent("claude", config['agents']['claude'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "test_chat"
    
    test_sequence = [
        ("hello world", "发送消息（默认配置）"),
        ("/param thinking low", "修改参数"),
        ("test message 2", "发送消息（新配置）"),
    ]
    
    for text, description in test_sequence:
        print(f"\n[User → Bot] {description}")
        print(f"> {text}")
        print("=" * 80)
        
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
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        await asyncio.sleep(0.2)
    
    print("\n✅ TEST 4 PASSED")
    return True

async def test_session_persistence():
    """测试会话持久化"""
    print("\n" + "="*80)
    print("TEST 5: 会话持久化测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {"sonnet": "claude-sonnet-4-5"},
                "default_model": "sonnet",
                "supported_params": {"thinking": "--thinking"},
                "default_params": {"thinking": "low"}
            }
        }
    }
    
    workspace = Path("/tmp/cli-gateway-test5")
    workspace.mkdir(parents=True, exist_ok=True)
    
    # Phase 1: Create session with custom config
    print("\n[Phase 1] 创建会话并配置")
    sm1 = SessionManager(workspace)
    session1 = sm1.create_session(
        user_id="123",
        chat_id="456",
        agent_name="claude",
        model="sonnet",
        params={"thinking": "high", "max_turns": "10"}
    )
    print(f"Created session: {session1.session_id}")
    print(f"Model: {session1.model}, Params: {session1.params}")
    
    # Phase 2: Reload and verify
    print("\n[Phase 2] 重新加载会话")
    sm2 = SessionManager(workspace)
    session2 = sm2.get_session(session1.session_id)
    
    if not session2:
        print("❌ Session not found after reload")
        return False
    
    if session2.model != "sonnet":
        print(f"❌ Expected model=sonnet, got {session2.model}")
        return False
    
    if session2.params.get("thinking") != "high":
        print(f"❌ Expected thinking=high, got {session2.params.get('thinking')}")
        return False
    
    print(f"✓ Loaded session: {session2.session_id}")
    print(f"✓ Model: {session2.model}, Params: {session2.params}")
    
    # Phase 3: Update and verify
    print("\n[Phase 3] 更新配置")
    sm2.update_model(session2.session_id, "opus")
    sm2.update_param(session2.session_id, "thinking", "low")
    
    # Phase 4: Reload again
    print("\n[Phase 4] 再次重新加载")
    sm3 = SessionManager(workspace)
    session3 = sm3.get_session(session1.session_id)
    
    if session3.model != "opus":
        print(f"❌ Expected model=opus, got {session3.model}")
        return False
    
    if session3.params.get("thinking") != "low":
        print(f"❌ Expected thinking=low, got {session3.params.get('thinking')}")
        return False
    
    print(f"✓ Updated session: {session3.session_id}")
    print(f"✓ Model: {session3.model}, Params: {session3.params}")
    
    print("\n✅ TEST 5 PASSED")
    return True

async def test_kapy_format():
    """测试 kapy 新格式命令"""
    print("\n" + "="*80)
    print("TEST 6: Kapy 新格式命令测试")
    print("="*80)
    
    config = {
        "agents": {
            "claude": {
                "command": "claude",
                "models": {
                    "sonnet": "claude-sonnet-4-5",
                    "opus": "claude-opus-4-6",
                },
                "default_model": "sonnet",
                "supported_params": {
                    "thinking": "--thinking"
                },
                "default_params": {
                    "thinking": "low"
                }
            }
        }
    }
    
    auth = Auth(channel_allowed={"telegram": ["123"]})
    workspace = Path("/tmp/cli-gateway-test6")
    workspace.mkdir(parents=True, exist_ok=True)
    session_manager = SessionManager(workspace)
    
    agents = {
        "claude": MockAgent("claude", config['agents']['claude'], workspace)
    }
    
    channel = FakeChannel()
    router = Router(auth, session_manager, agents, channel, config)
    
    user_id = "123"
    chat_id = "test_chat"
    
    # Create session
    msg = IncomingMessage(
        channel="telegram", chat_id=chat_id, user_id=user_id,
        text="init", is_private=True, is_reply_to_bot=False,
        is_mention_bot=False, attachments=[]
    )
    await router.handle_message(msg)
    
    test_sequence = [
        ("kapy help", "新格式帮助"),
        ("kapy params", "查看配置（新格式）"),
        ("kapy model opus", "切换模型（新格式）"),
        ("kapy param thinking high", "设置参数（新格式）"),
        ("kapy params", "确认配置更新"),
        ("kapy reset", "重置（新格式）"),
        ("/params", "确认重置（传统格式）"),
    ]
    
    for text, description in test_sequence:
        print(f"\n[User → Bot] {description}")
        print(f"> {text}")
        print("=" * 80)
        
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
        except Exception as e:
            print(f"❌ ERROR: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        await asyncio.sleep(0.1)
    
    # Verify final state
    session = session_manager.get_active_session(user_id)
    if session.model != "sonnet":
        print(f"❌ Expected model=sonnet after reset, got {session.model}")
        return False
    if session.params.get("thinking") != "low":
        print(f"❌ Expected thinking=low after reset, got {session.params.get('thinking')}")
        return False
    
    print("\n✅ TEST 6 PASSED")
    return True

async def main():
    """运行所有测试"""
    print("\n" + "="*80)
    print("CLI GATEWAY 测试套件")
    print("="*80)
    
    tests = [
        ("基础命令", test_basic_commands),
        ("模型切换", test_model_switching),
        ("参数配置", test_param_configuration),
        ("消息发送", test_message_with_config),
        ("会话持久化", test_session_persistence),
        ("Kapy 新格式", test_kapy_format),
    ]
    
    results = []
    
    for name, test_func in tests:
        try:
            result = await test_func()
            results.append((name, result))
        except Exception as e:
            print(f"\n❌ TEST FAILED: {name}")
            print(f"Exception: {e}")
            import traceback
            traceback.print_exc()
            results.append((name, False))
    
    # Summary
    print("\n" + "="*80)
    print("测试结果总结")
    print("="*80)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️ {total - passed} 个测试失败")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
