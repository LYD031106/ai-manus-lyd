# AI Manus 一键离线包

这个包冻结了当前定制版前端、后端和轻量 Sandbox，并携带 MongoDB 7、Redis 7 所需镜像。Sandbox 已内置 S-127 GML Skill、Python 和 `openpyxl`。

每个对话仍会创建独立 Sandbox 容器；各容器共享同一份镜像层，但运行时文件和进程互相隔离。空闲 Sandbox 按 `SANDBOX_TTL_MINUTES` 回收。

## 启动

```bash
tar -xzf ai-manus-oneclick-*.tar.gz
cd ai-manus-oneclick-*
./start.sh
```

服务器私有版已经包含当前 `.env`，可直接启动。仓库模板不含密钥：首次执行会生成 `.env`，填写必要配置后再次运行即可。

停止服务但保留对话数据：

```bash
./stop.sh
```

MongoDB 数据保存在 `manus-mongodb-data` 卷中。不要执行 `docker compose down -v`，否则会删除对话数据。
