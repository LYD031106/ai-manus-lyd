# AI Manus 远程 Docker 部署

本项目的正式部署方式是：服务器直接从 GitHub 拉取代码，服务器本地构建 Docker 镜像，服务器本地使用 Docker Compose 启动。Windows 本地电脑只负责 SSH 连接，不上传源码、不运行项目、不执行 Docker 构建。

详细的日常运维、备份、故障排查见 [Docker 运维手册](docs/operations-docker-zh.md)。

## 1. 固定信息

仓库：

~~~text
https://github.com/LYD031106/ai-manus-lyd.git
~~~

分支：

~~~text
codex/lightweight-sandbox
~~~

远程服务器源码目录示例：

~~~text
/opt/ai-manus
~~~

当前服务器实际运行目录：

~~~text
/opt/ai-manus-github-20260812
~~~

后续在这台服务器执行更新、构建和 Compose 操作时，应进入上述实际目录；`/opt/ai-manus` 是文档中的通用示例目录。

服务组成：

~~~text
Frontend + Backend + MongoDB + Redis
Backend 通过 Docker Socket 按对话创建独立 Sandbox
~~~

Mongo 数据卷名称：

~~~text
manus-mongodb-data
~~~

## 2. 首次部署

以下命令全部在远程服务器执行。

### 2.1 安装并检查依赖

~~~bash
docker version
docker compose version
git --version
free -h
df -h /
~~~

服务器建议至少有 2 GiB Swap 和 15 GiB 可用磁盘。公网只开放 Frontend 端口，默认是 80；不要把 MongoDB、Redis、Backend 端口暴露到公网。

### 2.2 直接从 GitHub 拉取

~~~bash
export APP_DIR=/opt/ai-manus
export REPO_URL=https://github.com/LYD031106/ai-manus-lyd.git
export BRANCH=codex/lightweight-sandbox

git clone --branch "$BRANCH" --single-branch "$REPO_URL" "$APP_DIR"
cd "$APP_DIR"
git rev-parse --short HEAD
~~~

如果目录已经存在，先查看：

~~~bash
git -C "$APP_DIR" status --short
git -C "$APP_DIR" remote -v
~~~

不要在没有确认的情况下删除已有目录。

### 2.3 配置正式环境

~~~bash
cd "$APP_DIR"
cp .env.example .env
chmod 600 .env
vim .env
~~~

至少配置：

~~~env
API_KEY=主对话模型API_KEY
API_BASE=https://api.deepseek.com/v1
MODEL_NAME=deepseek-chat
MODEL_PROVIDER=openai

PARSE_API_KEY=阿里云百炼API_KEY
PARSE_API_BASE=https://dashscope.aliyuncs.com/compatible-mode/v1
PARSE_MODEL=qwen-vl-max
PARSE_MAX_TOKENS=16000

MONGODB_URI=mongodb://mongodb:27017
MONGODB_DATABASE=manus
REDIS_HOST=redis
REDIS_PORT=6379
REDIS_DB=0

AUTH_PROVIDER=local
LOCAL_AUTH_EMAIL=admin@example.com
LOCAL_AUTH_PASSWORD=请设置管理员密码
JWT_SECRET_KEY=请替换为随机长字符串

SANDBOX_MEM_LIMIT=256m
SANDBOX_NANO_CPUS=500000000
SANDBOX_PIDS_LIMIT=64
SANDBOX_TTL_MINUTES=30
BROWSER_ENABLED=false
~~~

不要把真实 .env 提交到 GitHub。不要把 API Key 写入 Dockerfile、前端代码或 Docker 构建参数。

### 2.4 准备 Backend 基础镜像

当前 Backend 轻量 Dockerfile 基于已有 Backend 基础镜像，并覆盖项目代码。优先使用服务器已有的本地基础镜像：

~~~bash
export BASE_IMAGE=local/manus-backend-pre-skill:latest
docker image inspect "$BASE_IMAGE" >/dev/null
~~~

如果服务器没有该镜像，可尝试：

~~~bash
export BASE_IMAGE=simpleyyt/manus-backend:latest
~~~

### 2.5 在服务器构建 Backend 和 Sandbox

构建上下文必须是仓库根目录：

~~~bash
cd "$APP_DIR"

DOCKER_BUILDKIT=0 docker build \
  --build-arg BACKEND_BASE_IMAGE="$BASE_IMAGE" \
  -f backend/Dockerfile.lightweight \
  -t local/manus-backend-lightweight:latest \
  .

DOCKER_BUILDKIT=0 docker build \
  -f sandbox/Dockerfile.lightweight \
  -t local/manus-sandbox-lightweight:latest \
  .
~~~

Sandbox 镜像包含 Python、openpyxl、PDF/Word 依赖和 S-127 GML Skill。

前端代码发生变化时才重新构建 Frontend：

~~~bash
DOCKER_BUILDKIT=0 docker build \
  -f frontend/Dockerfile \
  -t local/manus-frontend-lightweight:latest \
  frontend
~~~

### 2.6 启动服务

~~~bash
cd "$APP_DIR"
docker compose \
  -p ai-manus \
  -f docker-compose-lightweight.yml \
  --env-file .env \
  up -d --no-build --remove-orphans
~~~

--no-build 表示启动时只使用已经构建好的镜像，不重新编译。

检查：

~~~bash
docker compose \
  -p ai-manus \
  -f docker-compose-lightweight.yml \
  --env-file .env \
  ps

curl -fsS http://127.0.0.1/api/v1/config/frontend
~~~

## 3. 更新部署

更新前先备份配置和 Mongo 数据：

~~~bash
cp "$APP_DIR/.env" /opt/ai-manus.env.backup.$(date +%Y%m%d-%H%M%S)
~~~

拉取 GitHub 最新分支：

~~~bash
cd "$APP_DIR"
git status --short
git fetch origin "$BRANCH"
git pull --ff-only origin "$BRANCH"
git rev-parse --short HEAD
~~~

服务器本地有未提交修改时，不要直接覆盖；先保存或确认这些修改不再需要。

重新构建变化的镜像：

~~~bash
cd "$APP_DIR"

DOCKER_BUILDKIT=0 docker build \
  --build-arg BACKEND_BASE_IMAGE="$BASE_IMAGE" \
  -f backend/Dockerfile.lightweight \
  -t local/manus-backend-lightweight:latest \
  .

DOCKER_BUILDKIT=0 docker build \
  -f sandbox/Dockerfile.lightweight \
  -t local/manus-sandbox-lightweight:latest \
  .
~~~

重新启动：

~~~bash
docker compose \
  -p ai-manus \
  -f docker-compose-lightweight.yml \
  --env-file .env \
  up -d --no-build --remove-orphans
~~~

Mongo 使用 manus-mongodb-data 卷，正常更新不会删除对话数据。

## 4. 部署验证

~~~bash
docker ps --format '{{.Names}}|{{.Image}}|{{.Status}}'
curl -fsS http://127.0.0.1/api/v1/config/frontend
~~~

验证新增工具：

~~~bash
docker exec ai-manus-backend-1 \
  test -f /app/app/domain/services/tools/parse_regulation.py

docker exec ai-manus-backend-1 \
  uv run python -c \
  'from app.domain.services.tools.parse_regulation import ParseRegulationToolkit; print(ParseRegulationToolkit.name)'
~~~

预期输出：

~~~text
parse_regulation
~~~

验证 Sandbox 依赖：

~~~bash
docker run --rm \
  --entrypoint python \
  local/manus-sandbox-lightweight:latest \
  -c 'import openpyxl, pdfplumber, docx; print(openpyxl.__version__)'
~~~

至少手工验证登录、新建对话、文件上传、Sandbox Python 执行和 PDF/Office 处理。

## 5. 回滚

~~~bash
cd "$APP_DIR"
git log --oneline --decorate -10
git checkout "$BRANCH"
git reset --hard <目标提交>
~~~

回滚后重新构建 Backend/Sandbox 并执行 Compose 启动命令。回滚镜像不会回滚 Mongo 数据；数据库回滚必须使用备份恢复。

## 6. 停止、日志和清理

~~~bash
docker compose -p ai-manus \
  -f "$APP_DIR/docker-compose-lightweight.yml" \
  --env-file "$APP_DIR/.env" logs --tail=100 backend

docker compose -p ai-manus \
  -f "$APP_DIR/docker-compose-lightweight.yml" \
  --env-file "$APP_DIR/.env" restart backend frontend

docker compose -p ai-manus \
  -f "$APP_DIR/docker-compose-lightweight.yml" \
  --env-file "$APP_DIR/.env" down
~~~

down 不会删除数据卷；不要使用 down -v。

清理前先确认当前 Compose 目录：

~~~bash
docker inspect ai-manus-backend-1 \
  --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}}'
~~~

只清理悬空镜像：

~~~bash
docker image prune -f
~~~

不要在不了解影响时执行 docker system prune -a --volumes，也不要删除 manus-mongodb-data。

## 7. 安全规则

1. API Key 只放服务器 .env，权限设置为 600。
2. 不把 .env、镜像包和完整密钥提交 GitHub。
3. MongoDB、Redis、Backend 不开放公网端口。
4. Backend 的 Docker Socket 权限很高，必须保护服务器登录权限。
5. Sandbox 保持内存、CPU、PID 限制。
6. 每次升级保留至少一个可回滚 Git 提交或镜像版本。
7. Mongo 数据定期备份，并保留异机副本。
