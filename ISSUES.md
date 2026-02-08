# Code Review Issues

## 核心层与架构 (Responsible: Connie)
1. **[P0] import time 在循环内** (`core/router.py`): `_forward_to_agent` 中每次 yield 都 import，性能反模式。
2. **[P0] 流式输出竞态** (`core/router.py`): 消息编辑无 retry/backoff，Telegram API 限流可能导致丢消息或异常。
3. **[P0] 字符串解析脆弱** (`core/router.py`): `text[9:]` 切片硬编码，对多字节字符偏移可能不安全。
4. **[P1] 旧 session 清理不全** (`core/router.py`): 仅内存销毁，磁盘 workspace 文件残留。
5. **[P1] Router 违反单一职责** (`core/router.py`): 耦合了解析、校验、session 管理、输出逻辑。
6. **[P1] 文件 I/O 无锁** (`core/session.py`): `_save()` 全量写 JSON 无互斥，并发易坏文件。
7. **[P1] Session 数量/过期未实现** (`core/session.py`): 配置项 `max_sessions_per_user` 和清理逻辑为空。
8. **[P2] 信号处理不规范** (`main.py`): asyncio 环境应使用 `loop.add_signal_handler`。
9. **[P2] Banner 样式硬编码** (`main.py`): 代理名称过长会导致破版。

## Agent 层与安全性 (Responsible: Sockey)
10. **[P0] Session 并发无互斥锁**: `is_busy` 仅标记，不防并发写入/串流竞态。
11. **[P1] `cancel()` 未实现**: 无法中断长任务，只能等超时。
12. **[P0] 超时仅 kill 主进程**: 未按进程组清理，可能残留子进程/僵尸进程。
13. **[P0] 日志泄露敏感信息**: 执行日志打印完整 args，可能包含 prompt/密钥/隐私。
14. **[P1] 鉴权粒度过粗**: `Auth` 仅校验 `user_id`，未限制 `chat_id`/会话上下文。
15. **[P1] 缺少速率限制**: 授权用户可高频请求导致资源滥用。
16. **[P1] 白名单变更不持久化**: `add/remove_user` 仅内存生效，重启丢失。
17. **[P1] 缺少权限分级（RBAC）**: 无 admin/operator/viewer 边界，运维风险高。
18. **[P1] 三套 Agent 实现高度重复**: 安全修复与行为变更易漏改、难一致。