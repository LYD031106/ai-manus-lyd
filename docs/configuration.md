# 📋 配置说明

## 配置项

### 模型提供商配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `API_KEY` | - | 是 | LLM 模型的 API 密钥 |
| `API_BASE` | `http://mockserver:8090/v1` | 否 | API 基础地址，用于指定模型服务的端点 |

### 模型配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `MODEL_PROVIDER` | `openai` | 否 | 模型提供商，决定底层使用哪个 LLM 集成（如 `openai`、`deepseek`、`anthropic`、`ollama`），仅在 `LLM_PROVIDER=langchain` 时生效 |
| `MODEL_NAME` | `deepseek-chat` | 是 | 要使用的模型名称 |
| `TEMPERATURE` | `0.7` | 否 | 模型响应的随机性程度，范围 0-1 |
| `MAX_TOKENS` | `2000` | 否 | 模型响应的最大 token 数量 |
| `LLM_PROVIDER` | `langchain` | 否 | LLM 网关实现：`langchain`（默认，经 `init_chat_model` 支持多种提供商）或 `openai`（直接使用官方 `openai` Python SDK 调用 OpenAI / 兼容端点） |
| `EXTRA_HEADERS` | - | 否 | 为模型请求附加的自定义 HTTP 头，JSON 对象字符串（如 `{"X-Api-Key":"xxx"}`），部分网关鉴权时需要 |

### 配置不同的模型 / 提供商

后端底层通过 **LangChain 的 [`init_chat_model`](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html)** 调用大模型，因此**只需通过环境变量即可切换不同的模型提供商，无需改动任何代码**：`MODEL_PROVIDER` 决定使用哪个集成，`MODEL_NAME` 指定具体模型，`API_KEY` / `API_BASE` 提供凭证与端点，`EXTRA_HEADERS` 可附加自定义请求头。

以下提供商已内置（对应的 LangChain 集成包已预装在 `backend/pyproject.toml` 中）：

| `MODEL_PROVIDER` | 说明 | 集成包 |
|------------------|------|--------|
| `openai` | OpenAI 及**所有 OpenAI 兼容端点**（DeepSeek、Moonshot、通义千问、vLLM、OneAPI、本地网关等），通过 `API_BASE` 指定端点 | `langchain-openai` |
| `deepseek` | DeepSeek 原生集成 | `langchain-deepseek` |
| `anthropic` | Anthropic Claude | `langchain-anthropic` |
| `ollama` | 本地 Ollama 运行的开源模型 | `langchain-ollama` |

**配置示例：**

- **OpenAI**
  ```env
  MODEL_PROVIDER=openai
  MODEL_NAME=gpt-4o
  API_KEY=sk-...
  # API_BASE 可省略以使用官方默认端点
  ```

- **OpenAI 兼容端点**（DeepSeek 官方 API / OneAPI / vLLM 等，最常见的接入方式）
  ```env
  MODEL_PROVIDER=openai
  MODEL_NAME=deepseek-chat
  API_BASE=https://api.deepseek.com/v1
  API_KEY=sk-...
  ```

- **DeepSeek 原生集成**
  ```env
  MODEL_PROVIDER=deepseek
  MODEL_NAME=deepseek-chat
  API_KEY=sk-...
  ```

- **Anthropic Claude**
  ```env
  MODEL_PROVIDER=anthropic
  MODEL_NAME=claude-3-5-sonnet-latest
  API_KEY=sk-ant-...
  ```

- **Ollama（本地）**
  ```env
  MODEL_PROVIDER=ollama
  MODEL_NAME=llama3.1
  API_BASE=http://host.docker.internal:11434
  API_KEY=ollama   # Ollama 无需真实密钥，但 API_KEY 必须非空以通过校验
  ```

> **接入更多提供商**：`init_chat_model` 还支持 Google Gemini、AWS Bedrock、Azure OpenAI、Mistral 等更多提供商。只需在 `backend/pyproject.toml` 增加对应的 `langchain-xxx` 集成包（如 `langchain-google-genai`）并重新构建镜像（`./build.sh` 或 `./dev.sh build`），再将 `MODEL_PROVIDER` 设为对应值即可。完整的提供商列表与命名参见 [LangChain `init_chat_model` 文档](https://python.langchain.com/api_reference/langchain/chat_models/langchain.chat_models.base.init_chat_model.html)。

### 切换 LLM 网关实现（`LLM_PROVIDER`）

后端在领域层通过统一的 `LLM` 接口调用大模型，具体实现由 `LLM_PROVIDER` 选择：

| `LLM_PROVIDER` | 说明 | 适用场景 |
|---------------|------|----------|
| `langchain`（默认） | 经 LangChain `init_chat_model` 调用，配合 `MODEL_PROVIDER` 支持 OpenAI、DeepSeek、Anthropic、Ollama 等多种提供商 | 需要多提供商、依赖 LangChain 生态（JSON 修复、重试等）时 |
| `openai` | 直接使用官方 `openai` Python SDK 调用 OpenAI 及**所有 OpenAI 兼容端点**（通过 `API_BASE`），不经过 LangChain | 只用 OpenAI / 兼容端点、希望减少依赖、更贴近原生 SDK 行为时 |

- 两种实现均消费同一套配置（`MODEL_NAME`、`API_KEY`、`API_BASE`、`TEMPERATURE`、`MAX_TOKENS`、`EXTRA_HEADERS`）。
- 选择 `openai` 时，`MODEL_PROVIDER` 被忽略（该实现始终使用 OpenAI SDK）。

**配置示例（使用 OpenAI SDK 直连 DeepSeek 兼容端点）：**

```env
LLM_PROVIDER=openai
MODEL_NAME=deepseek-chat
API_BASE=https://api.deepseek.com/v1
API_KEY=sk-...
```

### MongoDB 配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `MONGODB_URI` | `mongodb://mongodb:27017` | 否 | MongoDB 连接字符串 |
| `MONGODB_DATABASE` | `manus` | 否 | 数据库名称 |
| `MONGODB_USERNAME` | - | 否 | MongoDB 用户名 |
| `MONGODB_PASSWORD` | - | 否 | MongoDB 密码 |

> **注意**: MongoDB 配置项当前被注释，表示可能是可选功能或尚未完全实现。

### Redis 配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `REDIS_HOST` | `redis` | 否 | Redis 服务器地址 |
| `REDIS_PORT` | `6379` | 否 | Redis 服务器端口 |
| `REDIS_DB` | `0` | 否 | Redis 数据库编号 |
| `REDIS_PASSWORD` | - | 否 | Redis 密码 |

> **注意**: Redis 配置项当前被注释，表示可能是可选功能或尚未完全实现。

### 沙箱配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `SANDBOX_ADDRESS` | - | 否 | 沙箱服务器地址 |
| `SANDBOX_IMAGE` | `simpleyyt/manus-sandbox` | 否 | Docker 沙箱镜像名称 |
| `SANDBOX_NAME_PREFIX` | `sandbox` | 否 | 沙箱容器名称前缀 |
| `SANDBOX_TTL_MINUTES` | `30` | 否 | 沙箱生存时间（分钟） |
| `SANDBOX_NETWORK` | `manus-network` | 否 | Docker 网络名称 |
| `SANDBOX_CHROME_ARGS` | - | 否 | Chrome 浏览器启动参数 |
| `SANDBOX_HTTPS_PROXY` | - | 否 | HTTPS 代理设置 |
| `SANDBOX_HTTP_PROXY` | - | 否 | HTTP 代理设置 |
| `SANDBOX_NO_PROXY` | - | 否 | 不使用代理的地址列表 |

### Claw (OpenClaw) 配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `CLAW_ENABLED` | `false` | 否 | 是否启用 Claw 功能，设为 `true` 显示左侧面板入口 |
| `CLAW_IMAGE` | `simpleyyt/manus-claw` | 否 | Claw Docker 镜像名称 |
| `CLAW_NAME_PREFIX` | `manus-claw` | 否 | Claw 容器名称前缀 |
| `CLAW_TTL_SECONDS` | `3600` | 否 | Claw 容器生存时间（秒），设为 `0` 表示不限时 |
| `CLAW_NETWORK` | - | 否 | Claw 容器使用的 Docker 网络桥名称 |
| `CLAW_READY_TIMEOUT` | `300` | 否 | 等待 Claw 容器就绪的最大秒数（默认 5 分钟） |
| `CLAW_ADDRESS` | - | 否 | 固定 Claw 地址（开发环境使用，设置后跳过 Docker 容器创建） |
| `CLAW_API_KEY` | - | 否 | 静态 API 密钥（开发环境 / 固定容器使用） |
| `MANUS_API_BASE_URL` | `http://backend:8000` | 否 | 后端 API 地址，供 Claw 容器回调使用 |

### Web 检索

后端已移除 Web 检索工具，不需要配置搜索引擎。

### 认证配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `AUTH_PROVIDER` | `password` | 否 | 认证提供商 (`password`、`none` 或 `local`) |
| `SHOW_GITHUB_BUTTON` | `true` | 否 | 是否在前端显示 GitHub 按钮 |
| `GITHUB_REPOSITORY_URL` | `https://github.com/simpleyyt/ai-manus` | 否 | 前端 GitHub 按钮跳转地址 |
#### 密码认证配置

仅当 `AUTH_PROVIDER=password` 时使用：

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `PASSWORD_SALT` | - | 否 | 密码加密盐值 |
| `PASSWORD_HASH_ROUNDS` | `10` | 否 | 密码哈希轮数 |

#### 本地认证配置

仅当 `AUTH_PROVIDER=local` 时使用：

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `LOCAL_AUTH_EMAIL` | `admin@example.com` | 否 | 本地管理员邮箱 |
| `LOCAL_AUTH_PASSWORD` | `admin` | 否 | 本地管理员密码 |

### JWT 配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `JWT_SECRET_KEY` | `your-secret-key-here` | 是 | JWT 签名密钥（生产环境必须更改） |
| `JWT_ALGORITHM` | `HS256` | 否 | JWT 签名算法 |
| `JWT_ACCESS_TOKEN_EXPIRE_MINUTES` | `30` | 否 | 访问令牌过期时间（分钟） |
| `JWT_REFRESH_TOKEN_EXPIRE_DAYS` | `7` | 否 | 刷新令牌过期时间（天） |

### 邮箱配置

仅当 `AUTH_PROVIDER=password` 时使用：

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `EMAIL_HOST` | - | 否 | SMTP 服务器地址 |
| `EMAIL_PORT` | `587` | 否 | SMTP 服务器端口 |
| `EMAIL_USERNAME` | - | 否 | 邮箱用户名 |
| `EMAIL_PASSWORD` | - | 否 | 邮箱密码 |
| `EMAIL_FROM` | - | 否 | 发件人邮箱地址 |

### 任务后端配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `TASK_BACKEND` | `local` | 否 | Agent 任务执行后端：`local`（在 backend 进程内执行）或 `celery`（投递到分布式 Celery worker 执行） |
| `CELERY_BROKER_URL` | - | 否 | 自定义 Celery broker 地址，默认复用上面的 Redis 配置 |

#### 使用 Celery 任务后端

`TASK_BACKEND=celery` 时，agent 任务不再运行在 backend 进程内，而是投递到独立的 Celery worker 容器执行，backend 可以水平扩容多副本。事件仍通过 Redis Stream 流式返回，前端行为不变。

worker 容器复用 backend 镜像，通过 `start_worker.sh` 脚本启动，在 compose 中额外添加一个 worker 服务即可：

```yaml
  worker:
    image: simpleyyt/manus-backend:latest
    command: ["./start_worker.sh"]
    depends_on:
      - mongodb
      - redis
    restart: unless-stopped
    volumes:
      - /var/run/docker.sock:/var/run/docker.sock:ro
    networks:
      - manus-network
    env_file:
      - .env
    environment:
      - TASK_BACKEND=celery
```

说明：

- worker 需要与 backend 使用**相同的 `.env` 配置**（模型、MongoDB、Redis、沙箱等），因为它在执行任务时会直接访问这些服务。
- worker 需要挂载 `/var/run/docker.sock`，用于创建和连接沙箱容器；开发模式使用固定沙箱时（`SANDBOX_ADDRESS=sandbox`）可省略。
- 每个 agent 任务运行期间会独占一个 worker 进程，可通过环境变量 `CELERY_CONCURRENCY`（默认 `4`）控制可并行执行的 agent 会话数量，`CELERY_LOG_LEVEL`（默认 `INFO`）控制日志级别。
- 也可以不通过容器直接启动 worker：`cd backend && ./start_worker.sh`。

### MCP 配置

| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `MCP_CONFIG_PATH` | `/etc/mcp.json` | 否 | MCP 配置文件路径 |

### 日志配置
| 配置项 | 默认值 | 是否必需 | 说明 |
|--------|--------|----------|------|
| `LOG_LEVEL` | `INFO` | 否 | 日志级别 (`DEBUG`、`INFO`、`WARNING`、`ERROR`、`CRITICAL`) |


