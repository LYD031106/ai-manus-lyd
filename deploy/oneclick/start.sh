#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：未安装 Docker。" >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "错误：未安装 Docker Compose v2。" >&2
  exit 1
fi

if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  echo "已生成 .env，请填写 API_KEY、LOCAL_AUTH_PASSWORD 和 JWT_SECRET_KEY 后重新执行。" >&2
  exit 1
fi

bundle_version="$(sed -n 's/^BUNDLE_VERSION=//p' .env | tail -n 1)"
bundle_version="${bundle_version:-20260808}"
frontend_port="$(sed -n 's/^FRONTEND_PORT=//p' .env | tail -n 1)"
frontend_port="${frontend_port:-80}"

required_images=(
  "ai-manus-oneclick/frontend:$bundle_version"
  "ai-manus-oneclick/backend:$bundle_version"
  "ai-manus-oneclick/sandbox:$bundle_version"
  "mongo:7.0"
  "redis:7.0"
)

missing_image=false
for image in "${required_images[@]}"; do
  if ! docker image inspect "$image" >/dev/null 2>&1; then
    missing_image=true
    break
  fi
done

if [[ "$missing_image" == true ]]; then
  if [[ ! -f images.tar.gz ]]; then
    echo "错误：缺少 Docker 镜像，且当前目录没有 images.tar.gz。" >&2
    exit 1
  fi
  echo "首次启动：正在导入离线镜像……"
  gzip -dc images.tar.gz | docker load
fi

docker compose --env-file .env up -d --remove-orphans
echo "AI Manus 已启动：http://$(hostname -I | awk '{print $1}'):$frontend_port"
docker compose ps
