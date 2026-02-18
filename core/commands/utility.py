"""Utility commands: /start, /help, /history, /cancel."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command, registry
from utils.runtime_mode import is_system_mode, to_external_mode

if TYPE_CHECKING:
    from core.pipeline import Context


@command("/start", "启动 Gateway")
async def handle_start(ctx: "Context") -> None:
    await ctx.router._reply(ctx.message, "👋 CLI Gateway 已启动，发送 /help 查看命令。")


@command("/help", "显示帮助")
async def handle_help(ctx: "Context") -> None:
    await ctx.router._reply(
        ctx.message,
        "\n".join(
            [
                "📚 可用命令：",
                "",
                "💡 <b>两种格式</b>",
                "• 传统: <code>/model opus</code>",
                "• 新格式: <code>kapy model opus</code>",
                "",
                "<b>会话管理</b>",
                "agent [&lt;name&gt;] - 切换 agent 或查看当前 agent",
                "sessions - 列出所有会话",
                "current - 查看当前会话",
                "switch &lt;id&gt; - 切换到指定会话",
                "kill - 销毁当前会话",
                "name &lt;label&gt; - 为当前会话命名",
                "cancel - 取消当前执行",
                "history - 查看对话历史",
                "whoami - 查看当前身份与运行模式",
                "",
                "<b>模型配置</b>",
                "model [&lt;alias&gt;] - 切换模型或查看可用模型",
                "param [&lt;key&gt; &lt;value&gt;] - 设置参数或查看可用参数",
                "params - 查看当前配置",
                "reset - 重置为默认配置",
                "",
                "<b>文件管理</b>",
                "files - 列出当前会话输出文件",
                "download &lt;filename&gt; - 下载文件",
                "",
                "<b>系统审批（system 模式）</b>",
                "sudo status - 查看 sudo 开关状态",
                "sudo on - 触发 2FA，验证通过后 10 分钟 root 执行",
                "sudo off - 立即关闭 sudo",
                "sysauth plan &lt;action&gt; - 创建 2FA 审批请求",
                "sysauth approve &lt;id&gt; &lt;code&gt; - 提交 TOTP 审批",
                "sysauth status &lt;id&gt; - 查看审批状态",
                "sysauth setup start - 开始绑定 2FA（发送二维码）",
                "sysauth setup verify &lt;code&gt; - 提交绑定验证码并保存",
                "sysauth setup status - 查看绑定状态",
                "sysauth setup cancel - 取消绑定会话",
                "",
                "<b>示例</b>",
                "<code>kapy model opus</code>",
                "<code>kapy param thinking high</code>",
                "<code>kapy params</code>",
                "<code>kapy whoami</code>",
            ]
        ),
    )


@command("/history", "查看对话历史")
async def handle_history(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    if not current:
        await ctx.router._reply(ctx.message, "❌ 当前无活跃会话")
        return
    history = ctx.session_manager.get_history(current.session_id)
    if not history:
        await ctx.router._reply(ctx.message, "暂无对话历史")
        return
    lines = ["📜 对话历史："]
    for entry in history[-10:]:
        role = "👤" if entry.get("role") == "user" else "🤖"
        content = entry.get("content", "")[:100]
        lines.append(f"{role} {content}")
    await ctx.router._reply(ctx.message, "\n".join(lines))


@command("/cancel", "取消当前执行")
async def handle_cancel(ctx: "Context") -> None:
    scope_id = ctx.router.get_scope_id(ctx.message)
    current = ctx.session_manager.get_active_session_for_scope(scope_id)
    if not current:
        await ctx.router._reply(ctx.message, "❌ 当前无活跃会话")
        return
    agent = ctx.agents.get(current.agent_name)
    if not agent:
        await ctx.router._reply(ctx.message, "❌ Agent 不可用")
        return
    session_info = agent.get_session_info(current.session_id)
    if not session_info or not session_info.is_busy:
        await ctx.router._reply(ctx.message, "当前无正在执行的任务")
        return
    # Signal the streaming delivery loop to stop
    cancel_event = ctx.router.peek_cancel_event(current.session_id)
    if cancel_event:
        cancel_event.set()
    await agent.cancel(current.session_id)
    await ctx.router._reply(ctx.message, "✅ 已取消当前操作")


@command("/whoami", "查看当前身份与运行模式")
async def handle_whoami(ctx: "Context") -> None:
    runtime = (ctx.config or {}).get("runtime", {})
    mode = to_external_mode(runtime.get("mode", "session"))
    is_admin = ctx.auth.is_admin(ctx.user_id)
    is_system_admin = ctx.auth.is_system_admin(ctx.user_id)
    sudo_line = None
    if is_system_mode(runtime.get("mode", "session")):
        status = ctx.router.get_sudo_status(ctx.user_id, ctx.message.channel, ctx.message.chat_id)
        sudo_state = "on" if status.get("enabled") else "off"
        sudo_line = f"- sudo: <code>{sudo_state}</code>"
    await ctx.router._reply(
        ctx.message,
        "\n".join(
            [
                "🪪 当前身份信息",
                f"- user_id: <code>{ctx.user_id}</code>",
                f"- mode: <code>{mode}</code>",
                f"- admin: <code>{str(bool(is_admin)).lower()}</code>",
                f"- system_admin: <code>{str(bool(is_system_admin)).lower()}</code>",
            ]
            + ([sudo_line] if sudo_line else [])
        ),
    )
