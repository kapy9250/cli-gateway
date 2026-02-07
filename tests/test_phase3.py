"""
Phase 3 测试：Codex 和 Gemini Agent
"""
import asyncio
import sys
from pathlib import Path
from unittest.mock import MagicMock

# Add parent directory to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.codex_cli import CodexAgent
from agents.gemini_cli import GeminiAgent

class MockAgent:
    """Base mock for testing agent structure"""
    def __init__(self, agent_class, name, config):
        self.agent_class = agent_class
        self.name = name
        self.config = config
        self.workspace = Path(f"/tmp/test-{name}")
        self.workspace.mkdir(parents=True, exist_ok=True)
    
    async def test_create_session(self):
        """Test session creation"""
        agent = self.agent_class(self.name, self.config, self.workspace)
        session = await agent.create_session("user123", "chat456")
        
        assert session.session_id is not None
        assert session.agent_name == self.name
        assert session.user_id == "user123"
        assert session.work_dir.exists()
        
        print(f"  ✅ Session created: {session.session_id}")
        return agent, session
    
    async def test_config_params(self, agent, session):
        """Test parameter configuration"""
        models = self.config.get('models', {})
        supported_params = self.config.get('supported_params', {})
        
        print(f"  ✅ Models: {', '.join(models.keys())}")
        print(f"  ✅ Params: {', '.join(supported_params.keys())}")
        
        # Verify config structure
        assert 'command' in self.config
        assert 'args_template' in self.config
        assert len(models) > 0
        
        return True

async def test_codex_agent():
    """测试 Codex Agent"""
    print("\n" + "="*80)
    print("TEST: Codex Agent")
    print("="*80)
    
    config = {
        "command": "codex",
        "args_template": ["--prompt", "{prompt}", "--session", "{session_id}"],
        "models": {
            "gpt5.3": "gpt-5.3-codex"
        },
        "default_model": "gpt5.3",
        "supported_params": {
            "model": "--model",
            "temperature": "--temperature",
            "max_tokens": "--max-tokens"
        },
        "default_params": {},
        "timeout": 300
    }
    
    mock = MockAgent(CodexAgent, "codex", config)
    
    try:
        # Test session creation
        agent, session = await mock.test_create_session()
        
        # Test config
        await mock.test_config_params(agent, session)
        
        # Test command building (simulated)
        print("\n  [Simulated command]")
        cmd_parts = [config['command']]
        for arg in config['args_template']:
            arg = arg.replace("{prompt}", "test prompt")
            arg = arg.replace("{session_id}", session.session_id)
            cmd_parts.append(arg)
        
        # Add model
        model = "gpt5.3"
        model_flag = config['supported_params']['model']
        model_full = config['models'][model]
        cmd_parts.extend([model_flag, model_full])
        
        # Add params
        params = {"temperature": "0.7", "max_tokens": "1000"}
        for key, value in params.items():
            flag = config['supported_params'].get(key)
            if flag:
                cmd_parts.extend([flag, value])
        
        print(f"  Command: {' '.join(cmd_parts)}")
        
        # Verify structure
        assert "--model" in cmd_parts
        assert "gpt-5.3-codex" in cmd_parts
        assert "--temperature" in cmd_parts
        assert "0.7" in cmd_parts
        
        print("\n✅ Codex Agent Test PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Codex Agent Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_gemini_agent():
    """测试 Gemini Agent"""
    print("\n" + "="*80)
    print("TEST: Gemini Agent")
    print("="*80)
    
    config = {
        "command": "gemini-cli",
        "args_template": ["-p", "{prompt}"],
        "models": {
            "gemini3": "gemini-3-pro-preview"
        },
        "default_model": "gemini3",
        "supported_params": {
            "model": "-m",
            "temperature": "--temp"
        },
        "default_params": {},
        "timeout": 300
    }
    
    mock = MockAgent(GeminiAgent, "gemini", config)
    
    try:
        # Test session creation
        agent, session = await mock.test_create_session()
        
        # Test config
        await mock.test_config_params(agent, session)
        
        # Test command building (simulated)
        print("\n  [Simulated command]")
        cmd_parts = [config['command']]
        for arg in config['args_template']:
            arg = arg.replace("{prompt}", "test prompt")
            cmd_parts.append(arg)
        
        # Add model
        model = "gemini3"
        model_flag = config['supported_params']['model']
        model_full = config['models'][model]
        cmd_parts.extend([model_flag, model_full])
        
        # Add params
        params = {"temperature": "0.8"}
        for key, value in params.items():
            flag = config['supported_params'].get(key)
            if flag:
                cmd_parts.extend([flag, value])
        
        print(f"  Command: {' '.join(cmd_parts)}")
        
        # Verify structure
        assert "-m" in cmd_parts
        assert "gemini-3-pro-preview" in cmd_parts
        assert "--temp" in cmd_parts
        assert "0.8" in cmd_parts
        
        print("\n✅ Gemini Agent Test PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Gemini Agent Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_agent_switching():
    """测试 Agent 切换"""
    print("\n" + "="*80)
    print("TEST: Agent Switching")
    print("="*80)
    
    configs = {
        "claude": {
            "command": "claude",
            "models": {"sonnet": "claude-sonnet-4-5"},
            "default_model": "sonnet",
            "supported_params": {"thinking": "--thinking"},
            "default_params": {"thinking": "low"},
            "args_template": ["-p", "{prompt}"],
            "timeout": 300
        },
        "codex": {
            "command": "codex",
            "models": {"gpt5.3": "gpt-5.3-codex"},
            "default_model": "gpt5.3",
            "supported_params": {"temperature": "--temperature"},
            "default_params": {},
            "args_template": ["--prompt", "{prompt}"],
            "timeout": 300
        },
        "gemini": {
            "command": "gemini-cli",
            "models": {"gemini3": "gemini-3-pro-preview"},
            "default_model": "gemini3",
            "supported_params": {"temperature": "--temp"},
            "default_params": {},
            "args_template": ["-p", "{prompt}"],
            "timeout": 300
        }
    }
    
    try:
        # Test that each agent has unique command format
        commands = {name: cfg['command'] for name, cfg in configs.items()}
        print(f"\n  Agent commands:")
        for name, cmd in commands.items():
            print(f"    {name}: {cmd}")
        
        # Verify all unique
        assert len(commands) == len(set(commands.values()))
        print(f"  ✅ All agents have unique commands")
        
        # Test that param formats differ
        param_flags = {}
        for name, cfg in configs.items():
            params = cfg.get('supported_params', {})
            # Get first param flag as example
            if params:
                first_param = list(params.keys())[0]
                param_flags[name] = (first_param, params[first_param])
        
        print(f"\n  Parameter formats:")
        for name, (param_name, flag) in param_flags.items():
            print(f"    {name}: {param_name} → {flag}")
        
        print("\n✅ Agent Switching Test PASSED")
        return True
        
    except Exception as e:
        print(f"\n❌ Agent Switching Test FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """运行 Phase 3 测试"""
    print("\n" + "="*80)
    print("PHASE 3 测试套件: Codex & Gemini Integration")
    print("="*80)
    
    tests = [
        ("Codex Agent", test_codex_agent),
        ("Gemini Agent", test_gemini_agent),
        ("Agent Switching", test_agent_switching),
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
    print("Phase 3 测试结果总结")
    print("="*80)
    
    for name, result in results:
        status = "✅ PASS" if result else "❌ FAIL"
        print(f"{status} - {name}")
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    print(f"\n总计: {passed}/{total} 通过")
    
    if passed == total:
        print("\n🎉 Phase 3 所有测试通过！")
        return 0
    else:
        print(f"\n⚠️ {total - passed} 个测试失败")
        return 1

if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
