"""System operations commands."""

from __future__ import annotations

import base64
import hashlib
import json
import shlex
import time
from typing import TYPE_CHECKING, List, Optional, Tuple

from core.command_registry import command

if TYPE_CHECKING:
    from core.pipeline import Context

AUDIT_REDACTED_FIELDS = {"text", "output", "stderr", "stdout", "content"}


def _usage() -> str:
    return "\n".join(
        [
            "用法:",
            "• /sys journal [unit] [lines]",
            "• /sys read <path> [--max-bytes N] [--challenge <id>]",
            "• /sys cron list",
            "• /sys cron upsert <name> \"<schedule>\" \"<command>\" [--challenge <id>]",
            "• /sys cron delete <name> [--challenge <id>]",
            "• /sys docker <docker args...> [--challenge <id>]",
            "• /sys config write <path> <base64_content> [--challenge <id>]",
            "• /sys config append <path> <base64_content> [--challenge <id>]",
            "• /sys config delete <path> [--challenge <id>]",
            "• /sys config rollback <path> <backup_path> [--challenge <id>]",
        ]
    )


def _extract_challenge(parts: List[str]) -> Tuple[List[str], Optional[str], Optional[str]]:
    out = []
    challenge_id = None
    i = 0
    while i < len(parts):
        token = parts[i]
        if token == "--challenge":
            if i + 1 >= len(parts):
                return [], None, "--challenge 需要 challenge_id"
            challenge_id = parts[i + 1]
            i += 2
            continue
        out.append(token)
        i += 1
    return out, challenge_id, None


def _redacted_value(value) -> dict:
    if value is None:
        return {"redacted": True, "bytes": 0}
    if isinstance(value, str):
        raw = value.encode("utf-8", errors="replace")
    else:
        raw = str(value).encode("utf-8", errors="replace")
    return {
        "redacted": True,
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }


def _sanitize_for_audit(obj):
    if isinstance(obj, dict):
        cleaned = {}
        for k, v in obj.items():
            if str(k).lower() in AUDIT_REDACTED_FIELDS:
                cleaned[k] = _redacted_value(v)
            else:
                cleaned[k] = _sanitize_for_audit(v)
        return cleaned
    if isinstance(obj, list):
        return [_sanitize_for_audit(v) for v in obj]
    return obj


async def _require_approval(
    ctx: "Context",
    action_payload: dict,
    challenge_id: Optional[str],
    retry_cmd: str,
) -> Optional[str]:
    manager = ctx.two_factor
    if manager is None:
        await ctx.router._reply(ctx.message, "❌ two-factor manager 不可用")
        return None

    if not challenge_id:
        challenge = manager.create_challenge(ctx.user_id, action_payload)
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "🔐 该操作需要 2FA 审批",
                    f"- challenge_id: <code>{challenge.challenge_id}</code>",
                    "下一步:",
                    f"1) /sysauth approve {challenge.challenge_id} <totp_code>",
                    f"2) {retry_cmd} --challenge {challenge.challenge_id}",
                ]
            ),
        )
        return None

    ok, reason = manager.consume_approval(challenge_id, ctx.user_id, action_payload)
    if not ok:
        await ctx.router._reply(ctx.message, f"❌ 2FA 校验失败: <code>{reason}</code>")
        return None

    grant = getattr(ctx, "system_grant", None)
    if grant is None:
        # Local executor mode does not require signed grants.
        if getattr(ctx, "system_client", None) is None:
            return ""
        await ctx.router._reply(ctx.message, "❌ system grant signer 不可用")
        return None

    try:
        return grant.issue(ctx.user_id, action_payload)
    except Exception as e:
        await ctx.router._reply(ctx.message, f"❌ 生成系统授权票据失败: <code>{e}</code>")
        return None


def _execute_local(executor, action_payload: dict) -> dict:
    op = str(action_payload.get("op", "")).lower()
    if op == "journal":
        return executor.read_journal(
            unit=action_payload.get("unit"),
            lines=action_payload.get("lines", 100),
            since=action_payload.get("since"),
        )
    if op == "read_file":
        return executor.read_file(
            path=str(action_payload.get("path", "")),
            max_bytes=action_payload.get("max_bytes"),
        )
    if op == "cron_list":
        return executor.cron_list()
    if op == "cron_upsert":
        return executor.cron_upsert(
            name=str(action_payload.get("name", "")),
            schedule=str(action_payload.get("schedule", "")),
            command=str(action_payload.get("command", "")),
            user=str(action_payload.get("user", "root")),
        )
    if op == "cron_delete":
        return executor.cron_delete(name=str(action_payload.get("name", "")))
    if op == "docker_exec":
        args = action_payload.get("args") or []
        if not isinstance(args, list):
            return {"ok": False, "reason": "docker_args_not_list"}
        return executor.docker_exec([str(a) for a in args])
    if op == "config_write":
        return executor.write_file(
            path=str(action_payload.get("path", "")),
            content=str(action_payload.get("content", "")),
            append=False,
            create_backup=True,
        )
    if op == "config_append":
        return executor.write_file(
            path=str(action_payload.get("path", "")),
            content=str(action_payload.get("content", "")),
            append=True,
            create_backup=True,
        )
    if op == "config_delete":
        return executor.delete_file(str(action_payload.get("path", "")))
    if op == "config_rollback":
        return executor.restore_file(
            path=str(action_payload.get("path", "")),
            backup_path=str(action_payload.get("backup_path", "")),
        )
    return {"ok": False, "reason": "op_not_supported"}


async def _execute_action(ctx: "Context", action_payload: dict, grant_token: Optional[str] = None) -> dict:
    client = getattr(ctx, "system_client", None)
    if client is not None:
        return await client.execute(ctx.user_id, action_payload, grant_token=grant_token)

    executor = ctx.system_executor
    if executor is None:
        return {"ok": False, "reason": "system_executor_unavailable"}
    return _execute_local(executor, action_payload)


def _audit(ctx: "Context", action: str, payload: dict, result: dict) -> None:
    logger = getattr(ctx, "audit_logger", None)
    if logger is None:
        return
    event = {
        "ts": time.time(),
        "channel": ctx.message.channel,
        "chat_id": ctx.message.chat_id,
        "user_id": ctx.user_id,
        "action": action,
        "payload": _sanitize_for_audit(payload),
        "result": _sanitize_for_audit(result),
    }
    logger.info(json.dumps(event, ensure_ascii=False, sort_keys=True))


@command("/sys", "系统运维命令（system 模式）")
async def handle_sys(ctx: "Context") -> None:
    local_executor = ctx.system_executor
    remote_client = getattr(ctx, "system_client", None)
    if local_executor is None and remote_client is None:
        await ctx.router._reply(ctx.message, "❌ system backend 不可用")
        return

    text = (ctx.message.text or "").strip()
    try:
        parts = shlex.split(text)
    except Exception:
        await ctx.router._reply(ctx.message, "❌ 命令参数解析失败，请检查引号")
        return

    if len(parts) < 2:
        await ctx.router._reply(ctx.message, _usage())
        return

    normalized, challenge_id, challenge_err = _extract_challenge(parts)
    if challenge_err:
        await ctx.router._reply(ctx.message, f"❌ {challenge_err}")
        return

    sub = normalized[1].lower()
    if sub == "journal":
        unit = None
        lines = 100
        if len(normalized) >= 3:
            if normalized[2].isdigit():
                lines = int(normalized[2])
            else:
                unit = normalized[2]
        if len(normalized) >= 4 and normalized[3].isdigit():
            lines = int(normalized[3])

        action_payload = {"op": "journal", "unit": unit, "lines": lines}
        result = await _execute_action(ctx, action_payload)
        _audit(ctx, "journal", {"unit": unit, "lines": lines}, result)
        if not result.get("ok"):
            await ctx.router._reply(ctx.message, f"❌ journal 读取失败: <code>{result.get('reason')}</code>")
            return
        output = result.get("output", "")
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "📘 journal 输出",
                    f"- unit: <code>{unit or 'all'}</code>",
                    f"- lines: <code>{result.get('lines')}</code>",
                    "",
                    f"<pre><code>{output}</code></pre>",
                ]
            ),
        )
        return

    if sub == "read":
        if len(normalized) < 3:
            await ctx.router._reply(ctx.message, "用法: /sys read <path> [--max-bytes N] [--challenge <id>]")
            return
        path = normalized[2]
        max_bytes = 65536
        i = 3
        while i < len(normalized):
            if normalized[i] == "--max-bytes":
                if i + 1 >= len(normalized):
                    await ctx.router._reply(ctx.message, "❌ --max-bytes 需要整数")
                    return
                try:
                    max_bytes = int(normalized[i + 1])
                except Exception:
                    await ctx.router._reply(ctx.message, "❌ --max-bytes 必须是整数")
                    return
                i += 2
            else:
                await ctx.router._reply(ctx.message, f"❌ 未知参数: {normalized[i]}")
                return

        action_payload = {"op": "read_file", "path": path, "max_bytes": max_bytes}
        is_sensitive = bool(local_executor and local_executor.is_sensitive_path(path))
        if is_sensitive:
            retry_cmd = f"/sys read {path} --max-bytes {max_bytes}"
            grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
            if grant_token is None:
                _audit(
                    ctx,
                    "read_sensitive_pending_or_failed",
                    {"path": path, "max_bytes": max_bytes, "challenge_id": challenge_id},
                    {"ok": False, "reason": "2fa_required_or_failed"},
                )
                return
        else:
            grant_token = None

        result = await _execute_action(ctx, action_payload, grant_token=grant_token)
        _audit(ctx, "read_file", {"path": path, "max_bytes": max_bytes}, result)
        if not result.get("ok"):
            await ctx.router._reply(ctx.message, f"❌ 文件读取失败: <code>{result.get('reason')}</code>")
            return
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "📄 文件读取结果",
                    f"- path: <code>{result.get('path')}</code>",
                    f"- sensitive: <code>{str(bool(result.get('sensitive'))).lower()}</code>",
                    f"- size_bytes: <code>{result.get('size_bytes')}</code>",
                    f"- returned_bytes: <code>{result.get('returned_bytes')}</code>",
                    f"- truncated: <code>{str(bool(result.get('truncated'))).lower()}</code>",
                    "",
                    f"<pre><code>{result.get('text', '')}</code></pre>",
                ]
            ),
        )
        return

    if sub == "cron":
        if len(normalized) < 3:
            await ctx.router._reply(ctx.message, "用法: /sys cron list|upsert|delete ...")
            return
        op = normalized[2].lower()
        if op == "list":
            action_payload = {"op": "cron_list"}
            result = await _execute_action(ctx, action_payload)
            _audit(ctx, "cron_list", {}, result)
            if not result.get("ok"):
                await ctx.router._reply(ctx.message, f"❌ cron 列表失败: <code>{result.get('reason')}</code>")
                return
            items = result.get("items", [])
            body = "\n".join(f"- <code>{n}</code>" for n in items) if items else "(empty)"
            await ctx.router._reply(ctx.message, f"🕒 cron 任务列表\n{body}")
            return
        if op == "upsert":
            if len(normalized) < 6:
                await ctx.router._reply(
                    ctx.message,
                    "用法: /sys cron upsert <name> \"<schedule>\" \"<command>\" [--challenge <id>]",
                )
                return
            name = normalized[3]
            schedule = normalized[4]
            cron_command = normalized[5]
            action_payload = {
                "op": "cron_upsert",
                "name": name,
                "schedule": schedule,
                "command": cron_command,
            }
            retry_cmd = f"/sys cron upsert {name} \"{schedule}\" \"{cron_command}\""
            grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
            if grant_token is None:
                _audit(
                    ctx,
                    "cron_upsert_pending_or_failed",
                    {"name": name, "schedule": schedule, "command": cron_command, "challenge_id": challenge_id},
                    {"ok": False, "reason": "2fa_required_or_failed"},
                )
                return
            result = await _execute_action(ctx, action_payload, grant_token=grant_token)
            _audit(ctx, "cron_upsert", {"name": name, "schedule": schedule, "command": cron_command}, result)
            if not result.get("ok"):
                await ctx.router._reply(ctx.message, f"❌ cron 写入失败: <code>{result.get('reason')}</code>")
                return
            await ctx.router._reply(
                ctx.message,
                f"✅ cron 已更新: <code>{result.get('path')}</code>\nbackup: <code>{result.get('backup_path') or 'none'}</code>",
            )
            return
        if op == "delete":
            if len(normalized) < 4:
                await ctx.router._reply(ctx.message, "用法: /sys cron delete <name> [--challenge <id>]")
                return
            name = normalized[3]
            action_payload = {"op": "cron_delete", "name": name}
            retry_cmd = f"/sys cron delete {name}"
            grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
            if grant_token is None:
                _audit(
                    ctx,
                    "cron_delete_pending_or_failed",
                    {"name": name, "challenge_id": challenge_id},
                    {"ok": False, "reason": "2fa_required_or_failed"},
                )
                return
            result = await _execute_action(ctx, action_payload, grant_token=grant_token)
            _audit(ctx, "cron_delete", {"name": name}, result)
            if not result.get("ok"):
                await ctx.router._reply(ctx.message, f"❌ cron 删除失败: <code>{result.get('reason')}</code>")
                return
            await ctx.router._reply(ctx.message, f"✅ cron 已删除: <code>{result.get('path')}</code>")
            return
        await ctx.router._reply(ctx.message, "用法: /sys cron list|upsert|delete ...")
        return

    if sub == "docker":
        if len(normalized) < 3:
            await ctx.router._reply(ctx.message, "用法: /sys docker <docker args...> [--challenge <id>]")
            return
        docker_args = normalized[2:]
        action_payload = {"op": "docker_exec", "args": docker_args}
        retry_cmd = "/sys docker " + " ".join(shlex.quote(a) for a in docker_args)
        grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
        if grant_token is None:
            _audit(
                ctx,
                "docker_pending_or_failed",
                {"args": docker_args, "challenge_id": challenge_id},
                {"ok": False, "reason": "2fa_required_or_failed"},
            )
            return
        result = await _execute_action(ctx, action_payload, grant_token=grant_token)
        _audit(ctx, "docker_exec", {"args": docker_args}, result)
        if not result.get("ok"):
            await ctx.router._reply(
                ctx.message,
                "\n".join(
                    [
                        f"❌ docker 执行失败: <code>{result.get('reason', 'docker_failed')}</code>",
                        f"returncode: <code>{result.get('returncode')}</code>",
                        "",
                        f"<pre><code>{result.get('output', '')}</code></pre>",
                    ]
                ),
            )
            return
        await ctx.router._reply(
            ctx.message,
            "\n".join(
                [
                    "✅ docker 执行成功",
                    f"returncode: <code>{result.get('returncode')}</code>",
                    f"truncated: <code>{str(bool(result.get('truncated'))).lower()}</code>",
                    "",
                    f"<pre><code>{result.get('output', '')}</code></pre>",
                ]
            ),
        )
        return

    if sub == "config":
        if len(normalized) < 4:
            await ctx.router._reply(ctx.message, "用法: /sys config write|append|delete|rollback ...")
            return
        op = normalized[2].lower()
        path = normalized[3]
        if op == "delete":
            action_payload = {"op": "config_delete", "path": path}
            retry_cmd = f"/sys config delete {path}"
            grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
            if grant_token is None:
                _audit(
                    ctx,
                    "config_delete_pending_or_failed",
                    {"path": path, "challenge_id": challenge_id},
                    {"ok": False, "reason": "2fa_required_or_failed"},
                )
                return
            result = await _execute_action(ctx, action_payload, grant_token=grant_token)
            _audit(ctx, "config_delete", {"path": path}, result)
            if not result.get("ok"):
                await ctx.router._reply(ctx.message, f"❌ 配置删除失败: <code>{result.get('reason')}</code>")
                return
            await ctx.router._reply(ctx.message, f"✅ 配置已删除: <code>{result.get('path')}</code>")
            return

        if op == "rollback":
            if len(normalized) < 5:
                await ctx.router._reply(
                    ctx.message,
                    "用法: /sys config rollback <path> <backup_path> [--challenge <id>]",
                )
                return
            backup_path = normalized[4]
            action_payload = {
                "op": "config_rollback",
                "path": path,
                "backup_path": backup_path,
            }
            retry_cmd = f"/sys config rollback {path} {backup_path}"
            grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
            if grant_token is None:
                _audit(
                    ctx,
                    "config_rollback_pending_or_failed",
                    {"path": path, "backup_path": backup_path, "challenge_id": challenge_id},
                    {"ok": False, "reason": "2fa_required_or_failed"},
                )
                return
            result = await _execute_action(ctx, action_payload, grant_token=grant_token)
            _audit(ctx, "config_rollback", {"path": path, "backup_path": backup_path}, result)
            if not result.get("ok"):
                await ctx.router._reply(ctx.message, f"❌ 配置回滚失败: <code>{result.get('reason')}</code>")
                return
            await ctx.router._reply(
                ctx.message,
                f"✅ 配置已回滚: <code>{result.get('path')}</code>\nfrom: <code>{result.get('backup_path')}</code>",
            )
            return

        if op not in ("write", "append"):
            await ctx.router._reply(ctx.message, "用法: /sys config write|append|delete|rollback ...")
            return
        if len(normalized) < 5:
            await ctx.router._reply(
                ctx.message,
                "用法: /sys config write|append <path> <base64_content> [--challenge <id>]",
            )
            return
        encoded = normalized[4]
        try:
            raw = base64.b64decode(encoded.encode("ascii"), validate=True)
            content = raw.decode("utf-8")
        except Exception:
            await ctx.router._reply(ctx.message, "❌ base64_content 无效，必须是 UTF-8 文本的 base64 编码")
            return

        digest = hashlib.sha256(raw).hexdigest()
        action_payload = {
            "op": f"config_{op}",
            "path": path,
            "content": content,
            "content_sha256": digest,
        }
        retry_cmd = f"/sys config {op} {path} {encoded}"
        grant_token = await _require_approval(ctx, action_payload, challenge_id, retry_cmd)
        if grant_token is None:
            _audit(
                ctx,
                "config_write_pending_or_failed",
                {"op": op, "path": path, "content_sha256": digest, "challenge_id": challenge_id},
                {"ok": False, "reason": "2fa_required_or_failed"},
            )
            return

        result = await _execute_action(ctx, action_payload, grant_token=grant_token)
        _audit(ctx, f"config_{op}", {"path": path, "content_sha256": digest}, result)
        if not result.get("ok"):
            await ctx.router._reply(ctx.message, f"❌ 配置写入失败: <code>{result.get('reason')}</code>")
            return
        await ctx.router._reply(
            ctx.message,
            f"✅ 配置已更新: <code>{result.get('path')}</code>\nbackup: <code>{result.get('backup_path') or 'none'}</code>",
        )
        return

    await ctx.router._reply(ctx.message, _usage())
