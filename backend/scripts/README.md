# 会话历史维护

`repair_session_history.py` 修复中断的工具回复，并将旧 `sessions.events` 迁移到独立事件集合。它保留原消息和事件顺序，不执行工具或调用模型。缺失回复优先使用已保存的真实工具结果；没有证据时明确标记结果未知。

迁移使用 `session_events`、`session_event_payloads` 和 `session_event_blobs` GridFS。重复 shell 日志在单会话内去重，大事件不再受单个会话文档的 16 MiB 限制。应用层仍返回完整历史。

## 发布顺序

1. 构建包含本次修复的候选 Backend 镜像，保留旧镜像标签。
2. 在候选镜像中运行 `verify_history_storage.py`。它使用随机临时数据库验证真实 MongoDB/GridFS，结束后只删除自己的临时库。
3. 运行维护脚本 dry-run，检查计划。仅处理 `completed` / `idle` 会话，活动会话会跳过；歧义历史会报错。
4. 停止所有 Backend 写入进程，保存完整 MongoDB 备份并校验文件，再运行 `--apply`。脚本另外对每份原会话与 agent 文档保存 gzip BSON 备份，限制权限、fsync、回读验证后才写库。迁移后逐份核对历史哈希，并用原文档条件防止覆盖并发修改。
5. 再次 dry-run，确认 `repair_count` 与 `legacy_event_count` 为零，再启动新 Backend；检查登录、历史、状态及错误日志。

在 Backend 环境内运行（设置正常 MongoDB 配置，路径须为持久目录）：

```sh
export PYTHONPATH=/app
/app/.venv/bin/python -B /maintenance/verify_history_storage.py
/app/.venv/bin/python -B /maintenance/repair_session_history.py \
  --log-file /backups/dry-run.jsonl
/app/.venv/bin/python -B /maintenance/repair_session_history.py \
  --apply --backup-dir /backups/documents --log-file /backups/apply.jsonl
```

脚本可从仓库 `backend/scripts` 只读挂载到 `/maintenance`，备份目录单独挂载到 `/backups`。可重复指定 `--session-id` 限定范围；默认遍历全部会话。日志只记录标识、计数和哈希，不记录消息正文或工具参数。任一会话失败时进程返回非零；不要忽略退出码。

## 回滚

旧 Backend 只读取嵌入的 `sessions.events`，不能直接读取迁移后的历史。回滚旧镜像时必须同时恢复迁移前的数据库备份。应先停止写入，并另存当前数据库，避免丢失发布后的新对话；优先修复并继续使用新版存储。完整备份还原会覆盖备份之后的更新，必须确认恢复范围和时间点。保留迁移日志、原 BSON 备份、完整 MongoDB 备份和旧镜像直到确认发布稳定。
