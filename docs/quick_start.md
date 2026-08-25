# 🚀 快速上手

## 环境准备

本项目主要依赖Docker进行开发与部署，需要安装较新版本的Docker：

 * Docker 20.10+
 * Docker Compose

模型能力要求：

 * 支持 LangChain Chat Model（默认 `openai` 提供商）
 * 支持原生 Tool / Function Calling（计划与步骤结果通过结构化输出工具提交，不再依赖 Prompt 内嵌 JSON）

推荐使用具备稳定工具调用能力的 Deepseek 与 ChatGPT 模型。


## Docker 安装

### Windows & Mac 系统

按照官方要求安装 Docker Desktop ：https://docs.docker.com/desktop/

### Linux 系统

按照官方要求安装 Docker Engine：https://docs.docker.com/engine/

## 部署

使用 Docker Compose 进行部署，所有配置均通过 `.env` 文件（`env_file`）管理：

<!-- docker-compose-example.yml -->
```yaml
services:
  frontend:
    image: simpleyyt/manus-frontend
    ports:
      - "5173:80"
    depends_on:
      - backend
    restart: unless-stopped
    networks:
      - manus-network
    environment:
      - BACKEND_URL=http://backend:8000

  backend:
    image: simpleyyt/manus-backend
    depends_on:
      - sandbox
      - claw
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
      #- ./mcp.json:/etc/mcp.json # Mount MCP servers directory
    networks:
      - manus-network
    env_file:
      # All configuration is loaded from the .env file, see .env.example
      # More configuration options: https://docs.ai-manus.com/#/configuration
      - .env

  sandbox:
    image: simpleyyt/manus-sandbox
    command: /bin/sh -c "exit 0"  # prevent sandbox from starting, ensure image is pulled
    restart: "no"
    networks:
      - manus-network

  claw:
    image: simpleyyt/manus-claw
    entrypoint: /bin/sh -c "exit 0"  # prevent claw from starting, ensure image is pulled
    restart: "no"
    networks:
      - manus-network

  mongodb:
    image: mongo:7.0
    volumes:
      - mongodb_data:/data/db
    restart: unless-stopped
    #ports:
    #  - "27017:27017"
    networks:
      - manus-network

  redis:
    image: redis:7.0
    restart: unless-stopped
    networks:
      - manus-network

volumes:
  mongodb_data:
    name: manus-mongodb-data

networks:
  manus-network:
    name: manus-network
    driver: bridge
```
<!-- /docker-compose-example.yml -->

保存成 `docker-compose.yml` 文件。

### 创建 `.env` 配置文件

在 `docker-compose.yml` 同级目录下，基于 [`.env.example`](https://github.com/simpleyyt/ai-manus/blob/main/.env.example) 创建 `.env` 文件，至少需要修改 `API_KEY`，并根据模型服务调整 `API_BASE` 与 `MODEL_NAME`：

```ini
API_KEY=sk-xxxx
API_BASE=https://api.openai.com/v1
MODEL_NAME=gpt-4o
```

完整的 `.env.example` 如下（认证方式、沙箱、Claw 等更多配置项）：

<!-- .env.example -->
```ini
# Model provider configuration
API_KEY=
API_BASE=http://mockserver:8090/v1

# Model configuration
# MODEL_PROVIDER selects the LLM integration (via LangChain init_chat_model).
# Built-in providers: openai, deepseek, anthropic, ollama. OpenAI-compatible
# endpoints (DeepSeek / OneAPI / vLLM / ...) work with openai + API_BASE.
# See docs/configuration.md for per-provider examples.
MODEL_PROVIDER=openai
MODEL_NAME=deepseek-chat
TEMPERATURE=0.7
MAX_TOKENS=2000

# LLM gateway provider: langchain (default) or openai.
# - langchain: uses init_chat_model, supports many providers via MODEL_PROVIDER.
# - openai:    talks to OpenAI / OpenAI-compatible endpoints (API_BASE) directly
#              via the official openai Python SDK (MODEL_PROVIDER is ignored).
#LLM_PROVIDER=langchain

# MongoDB configuration
#MONGODB_URI=mongodb://mongodb:27017
#MONGODB_DATABASE=manus
#MONGODB_USERNAME=
#MONGODB_PASSWORD=

# Redis configuration
#REDIS_HOST=redis
#REDIS_PORT=6379
#REDIS_DB=0
#REDIS_PASSWORD=

# Sandbox configuration
#SANDBOX_ADDRESS=
SANDBOX_IMAGE=simpleyyt/manus-sandbox
SANDBOX_NAME_PREFIX=sandbox
SANDBOX_TTL_MINUTES=30
SANDBOX_NETWORK=manus-network
#SANDBOX_CHROME_ARGS=
#SANDBOX_HTTPS_PROXY=
#SANDBOX_HTTP_PROXY=
#SANDBOX_NO_PROXY=

# Browser engine configuration
# Options: playwright, browser_use (default)
# - playwright:   uses Playwright directly via CDP (stable, well-tested)
# - browser_use:  uses the browser_use library's BrowserSession via CDP
#                 (richer DOM state extraction via AI-friendly selector map)
#BROWSER_ENGINE=browser_use

# Web search is disabled in this deployment.
# Google Analytics configuration
# Set your Google Analytics Measurement ID (e.g. G-XXXXXXXXXX)
#GOOGLE_ANALYTICS_ID=

# Auth configuration
# Options: password, none, local
AUTH_PROVIDER=password

# Password auth configuration, only used when AUTH_PROVIDER=password
PASSWORD_SALT=
PASSWORD_HASH_ROUNDS=10

# Local auth configuration, only used when AUTH_PROVIDER=local
#LOCAL_AUTH_EMAIL=admin@example.com
#LOCAL_AUTH_PASSWORD=admin

# JWT configuration
JWT_SECRET_KEY=your-secret-key-here
JWT_ALGORITHM=HS256
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=30
JWT_REFRESH_TOKEN_EXPIRE_DAYS=7

# Email configuration
# Only used when AUTH_PROVIDER=password
#EMAIL_HOST=smtp.gmail.com
#EMAIL_PORT=587
#EMAIL_USERNAME=your-email@gmail.com
#EMAIL_PASSWORD=your-password
#EMAIL_FROM=your-email@gmail.com

# Claw (OpenClaw) configuration
# Enable or disable Claw feature (hides sidebar entry when false)
#CLAW_ENABLED=false
# Docker image used for Claw containers
#CLAW_IMAGE=simpleyyt/manus-claw
# Prefix for Claw container names
#CLAW_NAME_PREFIX=manus-claw
# Time-to-live for Claw containers in seconds (0 = unlimited)
#CLAW_TTL_SECONDS=3600
# Docker network bridge name for Claw containers
#CLAW_NETWORK=manus-network
# Max seconds to wait for Claw container to become ready
#CLAW_READY_TIMEOUT=300
# Fixed Claw address (for development; skips Docker container creation)
#CLAW_ADDRESS=
# Static API key for Claw (for development / fixed container)
#CLAW_API_KEY=
# Backend API URL used by Claw containers for callbacks
#MANUS_API_BASE_URL=http://backend:8000

# Extra headers for LLM API requests (JSON format)
#EXTRA_HEADERS={"X-Custom-Header": "value"}

# Task backend configuration
# local: run agent tasks in-process (default)
# celery: run agent tasks on distributed Celery workers
#         (requires a worker container, see docs/configuration.md)
#TASK_BACKEND=local
# Optional custom Celery broker URL (defaults to the Redis settings above)
#CELERY_BROKER_URL=

# MCP configuration
#MCP_CONFIG_PATH=/etc/mcp.json

# Log configuration
LOG_LEVEL=INFO
```
<!-- /.env.example -->

> **提示**：`env_file` 和 `environment` 可以同时使用，`environment` 中的值会覆盖 `env_file` 中的同名变量。完整的配置项说明请参阅[配置说明](configuration.md)。

### 启动服务

```bash
docker compose up -d
```

> 注意：如果提示 `sandbox-1 exited with code 0`，这是正常的，这是为了让 sandbox 镜像成功拉取到本地。

打开浏览器访问 <http://localhost:5173> 即可访问 Manus。

## 本地开发快速验证

开发调试推荐使用热重载栈：

```bash
cp .env.example .env
# 开发时可设 AUTH_PROVIDER=none，API_BASE 指向 mockserver 或真实 LLM
./dev.sh up -d
```

访问 <http://localhost:5173>。调试模式下全局只启动一个共享沙盒（`SANDBOX_ADDRESS=sandbox`）。更多见仓库根目录 README「开发指南」。
