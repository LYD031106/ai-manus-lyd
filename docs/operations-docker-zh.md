# AI Manus Docker 运维手册

本文档针对当前 LYD031106/ai-manus-lyd 的轻量部署版本，说明如何使用 Docker 进行部署、配置、升级、回滚、备份和故障排查。

当前版本保留“每个对话一个 Sandbox”的隔离方式，关闭浏览器和 Claw，使用轻量 Python Sandbox，并内置 S-127 GML Skill、openpyxl 以及 PDF/Word 文件处理依赖。

## 1. 架构概览

    浏览器 → Frontend :80 → Backend :8000
                                  ├── MongoDB 7
                                  ├── Redis 7
                                  └── Docker Socket → 每个对话一个 Sandbox

Compose 管理 Frontend、Backend、MongoDB 和 Redis。Sandbox 由 Backend 通过 Docker API 按需创建，不是长期运行的固定 Compose 服务。

每个对话的 Sandbox 拥有独立的进程和可写层，但共享 Sandbox 镜像的只读层，不会为每个对话复制完整镜像。对话删除或 TTL 到期后，Sandbox 容器会被回收，镜像不会被删除。

默认单容器限制：

    SANDBOX_MEM_LIMIT=256m
    SANDBOX_NANO_CPUS=500000000
    SANDBOX_PIDS_LIMIT=64
    SANDBOX_TTL_MINUTES=30

服务器只有约 1.8 GiB 内存时，不建议随意提高这些值。

## 2. 重要文件和发布包

仓库内的重要文件：

    backend/Dockerfile.lightweight       Backend 定制镜像
    sandbox/Dockerfile.lightweight       轻量 Sandbox 镜像
    builtin_skills/s127-gml/             内置 Skill
    docker-compose-lightweight.yml       源码构建用 Compose
    deploy/oneclick/                     离线一键包模板
    scripts/package-oneclick.sh          离线镜像打包脚本
    .env.example                         配置示例，不含密钥

发布包结构：

    ai-manus-oneclick-20260811/
    ├── docker-compose.yml
    ├── start.sh
    ├── stop.sh
    ├── .env
    ├── .env.example
    ├── images.tar.gz
    ├── IMAGES
    └── SHA256SUMS

.env 和 images.tar.gz 不应提交到 GitHub。服务器私有发布包可以包含 .env，但必须限制为 root 可读。

## 3. 服务器前置条件

建议使用 Linux 服务器并安装：

- Docker Engine
- Docker Compose v2（命令是 docker compose）
- 至少 2 GiB Swap
- 至少 15 GiB 可用磁盘空间

检查 Docker：

    docker version
    docker compose version
    docker info

生产环境默认只对外开放 Frontend 的 80/tcp。MongoDB、Redis 和 Backend 不应直接暴露公网。

## 4. 离线发布包首次部署

### 4.1 校验发布包

将以下文件上传到服务器同一目录：

    ai-manus-oneclick-20260811.tar.gz
    ai-manus-oneclick-20260811.tar.gz.sha256

校验：

    sha256sum -c ai-manus-oneclick-20260811.tar.gz.sha256

只有看到 OK 才继续。

### 4.2 解压并启动

    mkdir -p /opt/ai-manus/releases
    tar -xzf ai-manus-oneclick-20260811.tar.gz -C /opt/ai-manus/releases
    cd /opt/ai-manus/releases/ai-manus-oneclick-20260811
    chmod +x start.sh stop.sh
    ./start.sh

start.sh 会检查 Docker/Compose、检查 .env、导入缺失镜像，然后启动 MongoDB、Redis、Backend 和 Frontend。

如果使用不含私有配置的安全模板：

    cp .env.example .env
    chmod 600 .env
    vim .env
    ./start.sh

## 5. 正式环境配置

### 5.1 主对话模型

主模型使用 OpenAI 兼容接口：

    API_KEY=主模型API密钥
    API_BASE=https://api.deepseek.com/v1
    MODEL_NAME=当前模型名称
    MODEL_PROVIDER=openai

不同供应商只需要修改这几个变量。密钥不要写入 Dockerfile、源码或前端代码。

### 5.2 新增法规图件解析 Skill

当前 parse_regulation 使用阿里云百炼 OpenAI 兼容视觉接口：

    PARSE_API_KEY=百炼API密钥
    PARSE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
    PARSE_MODEL=qwen-vl-max
    PARSE_MAX_TOKENS=16000

PARSE_API_KEY 没有配置时，代码会回退到 API_KEY。由于主模型和百炼通常不是同一个供应商，生产环境建议分开配置。

这个工具只接收图片。PDF、Word、Excel 等文件先由 Sandbox 内的 parse_office.py 提取文本或渲染图片，再把需要视觉理解的图片发送给模型：

    PDF / Word / Excel → Sandbox parse_office.py → PNG
                                      → parse_regulation → Markdown / JSON

### 5.3 MongoDB、Redis 和认证

Compose 服务名就是容器内主机名：

    MONGODB_URI=mongodb://mongodb:27017
    MONGODB_DATABASE=manus
    REDIS_HOST=redis
    REDIS_PORT=6379
    REDIS_DB=0

不要在 Backend 中使用 localhost 连接 MongoDB 或 Redis。

认证示例：

    AUTH_PROVIDER=local
    LOCAL_AUTH_EMAIL=admin@example.com
    LOCAL_AUTH_PASSWORD=请设置管理员密码
    JWT_SECRET_KEY=长度足够的随机字符串
    JWT_ALGORITHM=HS256
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES=1440
    JWT_REFRESH_TOKEN_EXPIRE_DAYS=30

修改 JWT_SECRET_KEY 后，旧登录令牌通常会失效。

### 5.4 Sandbox

    SANDBOX_IMAGE=ai-manus-oneclick/sandbox:20260811
    SANDBOX_NAME_PREFIX=sandbox-
    SANDBOX_TTL_MINUTES=30
    SANDBOX_NETWORK=manus-network
    SANDBOX_MEM_LIMIT=256m
    SANDBOX_NANO_CPUS=500000000
    SANDBOX_PIDS_LIMIT=64
    BROWSER_ENABLED=false

发布包的 Compose 文件会显式设置当前版本的 SANDBOX_IMAGE。升级 Sandbox 必须重新构建并发布镜像，不能只修改 .env。

### 5.5 配置优先级

Compose 同时使用 env_file 和 environment：

    backend:
      env_file:
        - .env
      environment:
        BROWSER_ENABLED: "false"
        SANDBOX_IMAGE: ai-manus-oneclick/sandbox:20260811

environment 中明确写出的值优先级更高。检查最终配置：

    docker compose --env-file .env config

该命令可能打印密钥，只在服务器本地查看，不要把输出粘贴到聊天或 GitHub。

## 6. 日常运维

进入发布目录：

    cd /opt/ai-manus/releases/ai-manus-oneclick-20260811

查看状态：

    docker compose --env-file .env ps
    docker ps --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

查看日志：

    docker compose --env-file .env logs --tail=100 backend
    docker compose --env-file .env logs -f backend
    docker compose --env-file .env logs --tail=100 frontend
    docker logs --tail=100 ai-manus-mongodb-1

重启应用：

    docker compose --env-file .env restart backend frontend

停止服务但保留数据：

    ./stop.sh

不要执行 docker compose down -v，因为 -v 可能删除 Mongo 数据卷。

接口检查：

    curl -fsS http://127.0.0.1/api/v1/config/frontend

## 7. Sandbox 运维

查看当前 Sandbox：

    docker ps --filter 'name=sandbox-' \
      --format 'table {{.Names}}\t{{.Image}}\t{{.Status}}'

查看单个 Sandbox 限制：

    docker inspect sandbox-容器名 \
      --format 'memory={{.HostConfig.Memory}} nano_cpus={{.HostConfig.NanoCpus}} pids={{.HostConfig.PidsLimit}}'

检查镜像依赖：

    docker run --rm --entrypoint python \
      ai-manus-oneclick/sandbox:20260811 \
      -c 'import openpyxl, pdfplumber, docx; print(openpyxl.__version__)'

Skill 路径：

    /opt/skills/s127-gml

Sandbox 工作目录通常包括：

    /home/ubuntu/upload
    /home/ubuntu/workspace

进入容器排查：

    docker exec -it sandbox-容器名 bash

## 8. 数据备份和恢复

### 8.1 确认 Mongo 数据卷

    docker volume inspect manus-mongodb-data

当前卷名固定为 manus-mongodb-data。

### 8.2 停机备份

为保证文件一致性，先停止应用写入：

    ./stop.sh
    mkdir -p /opt/ai-manus/backups

备份 Mongo 数据卷：

    docker run --rm \
      --volumes-from ai-manus-mongodb-1 \
      -v /opt/ai-manus/backups:/backup \
      mongo:7.0 \
      tar czf /backup/mongodb-$(date +%Y%m%d-%H%M%S).tar.gz -C /data/db .

然后启动：

    ./start.sh

### 8.3 恢复

恢复会覆盖当前数据。先停服并额外备份当前卷，再执行：

    ./stop.sh
    docker run --rm \
      --volumes-from ai-manus-mongodb-1 \
      -v /opt/ai-manus/backups:/backup \
      mongo:7.0 \
      sh -c 'rm -rf /data/db/* && tar xzf /backup/mongodb-备份文件.tar.gz -C /data/db'
    ./start.sh

正式环境建议保留每日备份、最近 7～14 个版本和一份异机副本。

## 9. 更新和回滚

推荐把源码、镜像、配置和数据分开：

    Git 分支       源码
    Docker 镜像    运行环境和代码
    .env           环境配置和密钥
    Mongo volume   业务数据

### 9.1 构建新版本

在构建服务器上使用目标提交源码。Backend 和 Sandbox 构建上下文必须是项目根目录：

    docker build \
      --build-arg BACKEND_BASE_IMAGE=local/manus-backend-pre-skill:latest \
      -f backend/Dockerfile.lightweight \
      -t local/manus-backend-lightweight:latest .

    docker build \
      -f sandbox/Dockerfile.lightweight \
      -t local/manus-sandbox-lightweight:latest .

前端没有代码变化时可复用已验证镜像；前端变化时再执行前端构建。

### 9.2 打包和部署

    SOURCE_ENV=/opt/ai-manus/.env \
      bash scripts/package-oneclick.sh 20260812 /opt/ai-manus/releases

    tar -xzf ai-manus-oneclick-20260812.tar.gz -C /opt/ai-manus/releases
    cd /opt/ai-manus/releases/ai-manus-oneclick-20260812
    sha256sum -c SHA256SUMS
    ./start.sh

Compose 会重建发生变化的 Backend/Frontend 容器，但继续使用原来的 manus-mongodb-data 卷。

升级后必须检查：

    docker compose ps
    curl -fsS http://127.0.0.1/api/v1/config/frontend
    docker logs --tail=100 ai-manus-backend-1

至少验证登录、创建对话、文本上传、PDF/Office 上传和 Python 执行。

### 9.3 回滚

发布包是不可变版本。回滚时直接启动旧包：

    cd /opt/ai-manus/releases/ai-manus-oneclick-20260810
    ./start.sh

只回滚镜像不会回滚 Mongo 数据；数据库回滚必须使用备份恢复。

## 10. 资源和磁盘维护

    free -h
    swapon --show
    docker stats --no-stream
    docker system df
    docker system df -v
    du -sh /var/lib/docker 2>/dev/null || true
    du -sh /opt/ai-manus /opt/ai-manus/releases

删除旧发布包前先确认：

    ls -lh /opt/ai-manus/releases

清理无用镜像前先查看：

    docker image ls
    docker image prune

不要在不了解影响时执行：

    docker system prune -a --volumes

该命令可能删除回滚镜像、未使用卷和其他项目数据。

## 11. 常见故障

### 11.1 前端 502

重启后 Backend 要等待 Mongo 和 Redis 健康检查，前端可能短暂 502：

    docker compose ps
    docker logs --tail=150 ai-manus-backend-1
    curl -fsS http://127.0.0.1/api/v1/config/frontend

Frontend 到 Backend 的内部地址应为 http://backend:8000。

### 11.2 Unknown tool: parse_regulation

检查实际镜像和文件：

    docker inspect ai-manus-backend-1 --format '{{.Config.Image}}'
    docker exec ai-manus-backend-1 test -f /app/app/domain/services/tools/parse_regulation.py

文件不存在说明仍在运行旧 Backend 镜像，需要加载新发布包并重新启动。

### 11.3 Sandbox 缺少依赖

    docker inspect sandbox-容器名 --format '{{.Config.Image}}'
    docker run --rm --entrypoint python \
      ai-manus-oneclick/sandbox:版本号 \
      -c 'import openpyxl, pdfplumber, docx; print("ok")'

如果版本正确但依赖缺失，说明打包时使用了旧镜像标签，需要重新构建 Sandbox。

### 11.4 文件上传失败

    df -h
    docker stats --no-stream
    docker logs --tail=150 ai-manus-backend-1
    docker ps --filter 'name=sandbox-'

重点关注磁盘写满、Sandbox 创建失败、内存不足和 Docker Socket 权限。

### 11.5 重启后对话消失

先确认没有执行过 down -v，再确认卷仍存在：

    docker volume inspect manus-mongodb-data
    docker logs --tail=150 ai-manus-mongodb-1

容器重建不会自动删除这个卷；卷被删除时只能从备份恢复。

### 11.6 内存不足

    free -h
    docker stats --no-stream
    docker ps --filter 'name=sandbox-'

处理顺序：清理已结束 Sandbox、检查未回收容器、降低单容器上限、限制并发、保证 Swap。不要单纯无限增加 Swap，Swap 抖动会显著降低 Backend 和 Mongo 响应速度。

## 12. 安全要求

1. .env 使用 chmod 600，只允许部署用户读取。
2. API Key 不进入 Git、Dockerfile、前端代码和镜像构建参数。
3. 不向公网暴露 MongoDB、Redis 和 Backend。
4. Docker Socket 只读挂载不代表 Docker API 只读；Backend 仍可创建和管理宿主机容器。
5. 通过 Sandbox 的内存、CPU 和 PID 限制降低失控任务的影响。
6. 定期轮换 API Key，并删除不再使用的旧 Key。
7. 发布包含密钥时，只通过受控服务器或加密通道传输。

## 13. 发布检查清单

    [ ] 目标 Git 提交已确认
    [ ] Backend 镜像包含最新代码
    [ ] Sandbox 镜像包含 openpyxl 和 Skill
    [ ] 版本号已更新
    [ ] .env 未进入 Git
    [ ] API_BASE / MODEL_NAME 正确
    [ ] PARSE_API_KEY / PARSE_API_BASE / PARSE_MODEL 正确
    [ ] SHA256 校验通过
    [ ] Mongo 数据已备份
    [ ] docker compose ps 全部正常
    [ ] 前端 API 返回 200
    [ ] 登录正常
    [ ] 新建对话正常
    [ ] 文件上传正常
    [ ] Python 执行正常
    [ ] 至少保留一个可回滚旧版本

查看当前镜像版本：

    docker inspect ai-manus-backend-1 --format '{{.Config.Image}}'
    docker inspect ai-manus-frontend-1 --format '{{.Config.Image}}'

