"""Model/param configuration commands: /model, /param, /params, /reset."""

from __future__ import annotations

from typing import TYPE_CHECKING

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context


@command("/model", "切换模型或查看可用模型")
async def handle_model(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    router = ctx.router
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    active_agent_name = current.agent_name if current else router._get_user_agent(ctx.message.user_id)
    agent_config = ctx.config["agents"].get(active_agent_name, {})
    models = agent_config.get("models", {})

    if len(parts) < 2:
        if models:
            current_model = current.model if current else None
            lines = [f"<b>{active_agent_name} 可用模型：</b>"]
            for alias, full_name in models.items():
                marker = "✅" if current_model == alias else "-"
                lines.append(f"{marker} <code>{alias}</code> ({full_name})")
            await router._reply(ctx.message, "\n".join(lines))
        else:
            await router._reply(ctx.message, "该 agent 无可切换模型")
        return

    model_alias = parts[1].strip().lower()
    if model_alias not in models:
        await router._reply(
            ctx.message,
            f"❌ 模型不存在: {model_alias}\n可用: {', '.join(models.keys())}",
        )
        return

    if current:
        ctx.session_manager.update_model(current.session_id, model_alias)
        await router._reply(ctx.message, f"✅ 已切换模型: {model_alias} ({models[model_alias]})")
    else:
        router._user_model_pref[str(ctx.message.user_id)] = model_alias
        await router._reply(
            ctx.message,
            f"✅ 已设置模型偏好: {model_alias} ({models[model_alias]})，下次会话生效",
        )


@command("/param", "设置参数或查看可用参数")
async def handle_param(ctx: "Context") -> None:
    parts = (ctx.message.text or "").strip().split()
    router = ctx.router
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await router._reply(ctx.message, "❌ 当前无活跃会话")
        return

    if len(parts) < 2:
        agent_config = ctx.config["agents"].get(current.agent_name, {})
        supported = agent_config.get("supported_params", {})
        if supported:
            lines = [f"<b>{current.agent_name} 支持的参数：</b>"]
            for key in supported.keys():
                current_value = current.params.get(key, "(未设置)")
                lines.append(f"- <code>{key}</code>: {current_value}")
            lines.append("\n用法: /param &lt;key&gt; &lt;value&gt;")
            await router._reply(ctx.message, "\n".join(lines))
        else:
            await router._reply(ctx.message, "该 agent 无可配置参数")
        return

    if len(parts) < 3:
        await router._reply(ctx.message, "用法: /param &lt;key&gt; &lt;value&gt;")
        return

    key = parts[1].strip()
    value = parts[2].strip()
    agent_config = ctx.config["agents"].get(current.agent_name, {})
    supported = agent_config.get("supported_params", {})

    if key not in supported:
        await router._reply(
            ctx.message,
            f"❌ {current.agent_name} 不支持参数 {key}\n支持: {', '.join(supported.keys())}",
        )
        return

    ctx.session_manager.update_param(current.session_id, key, value)
    await router._reply(ctx.message, f"✅ 已设置 {key} = {value}")


@command("/params", "查看当前配置")
async def handle_params(ctx: "Context") -> None:
    router = ctx.router
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await router._reply(ctx.message, "❌ 当前无活跃会话")
        return

    agent_config = ctx.config["agents"].get(current.agent_name, {})
    models = agent_config.get("models", {})

    lines = [
        "<b>当前配置</b>",
        f"会话: <code>{current.session_id}</code>",
        f"Agent: {current.agent_name}",
    ]
    if current.model:
        model_full = models.get(current.model, current.model)
        lines.append(f"模型: <code>{current.model}</code> ({model_full})")
    else:
        lines.append("模型: (默认)")

    if current.params:
        lines.append("\n<b>参数：</b>")
        for key, value in current.params.items():
            lines.append(f"- <code>{key}</code>: {value}")
    else:
        lines.append("\n参数: (无)")

    await router._reply(ctx.message, "\n".join(lines))


@command("/reset", "重置为默认配置")
async def handle_reset(ctx: "Context") -> None:
    router = ctx.router
    current = ctx.session_manager.get_active_session(ctx.message.user_id)
    if not current:
        await router._reply(ctx.message, "❌ 当前无活跃会话")
        return

    agent_config = ctx.config["agents"].get(current.agent_name, {})
    default_model = agent_config.get("default_model")
    default_params = agent_config.get("default_params", {}).copy()

    ctx.session_manager.update_model(current.session_id, default_model)
    ctx.session_manager.reset_params(current.session_id, default_params)

    await router._reply(ctx.message, "✅ 已重置为默认配置")
