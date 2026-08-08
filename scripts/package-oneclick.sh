#!/usr/bin/env bash
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="${1:-$(date +%Y%m%d%H%M%S)}"
output_dir="${2:-$repo_dir/releases}"
source_env="${SOURCE_ENV:-$repo_dir/.env}"
bundle_name="ai-manus-oneclick-$version"
stage_dir="$(mktemp -d)"
bundle_dir="$stage_dir/$bundle_name"

cleanup() {
  rm -rf -- "$stage_dir"
}
trap cleanup EXIT

mkdir -p "$bundle_dir" "$output_dir"
cp "$repo_dir/deploy/oneclick/docker-compose.yml" "$bundle_dir/"
cp "$repo_dir/deploy/oneclick/start.sh" "$bundle_dir/"
cp "$repo_dir/deploy/oneclick/stop.sh" "$bundle_dir/"
cp "$repo_dir/deploy/oneclick/README.md" "$bundle_dir/"
cp "$repo_dir/deploy/oneclick/.env.example" "$bundle_dir/"
chmod +x "$bundle_dir/start.sh" "$bundle_dir/stop.sh"

sed -i "s/^BUNDLE_VERSION=.*/BUNDLE_VERSION=$version/" "$bundle_dir/.env.example"
sed -i "s/:20260808/:$version/g" "$bundle_dir/docker-compose.yml" "$bundle_dir/.env.example"

docker tag local/manus-frontend-lightweight:latest "ai-manus-oneclick/frontend:$version"
docker tag local/manus-backend-lightweight:latest "ai-manus-oneclick/backend:$version"
docker tag local/manus-sandbox-lightweight:latest "ai-manus-oneclick/sandbox:$version"

if [[ -f "$source_env" ]]; then
  cp "$source_env" "$bundle_dir/.env"
  chmod 600 "$bundle_dir/.env"
  if grep -q '^BUNDLE_VERSION=' "$bundle_dir/.env"; then
    sed -i "s/^BUNDLE_VERSION=.*/BUNDLE_VERSION=$version/" "$bundle_dir/.env"
  else
    printf '\nBUNDLE_VERSION=%s\n' "$version" >> "$bundle_dir/.env"
  fi
fi

images=(
  "ai-manus-oneclick/frontend:$version"
  "ai-manus-oneclick/backend:$version"
  "ai-manus-oneclick/sandbox:$version"
  "mongo:7.0"
  "redis:7.0"
)

printf '%s\n' "${images[@]}" > "$bundle_dir/IMAGES"
echo "正在导出并压缩 Docker 镜像……"
docker save "${images[@]}" | gzip -1 > "$bundle_dir/images.tar.gz"

(
  cd "$bundle_dir"
  sha256sum images.tar.gz docker-compose.yml start.sh stop.sh > SHA256SUMS
)

archive="$output_dir/$bundle_name.tar.gz"
tar -C "$stage_dir" -czf "$archive" "$bundle_name"
sha256sum "$archive" > "$archive.sha256"
chmod 600 "$archive" "$archive.sha256"

echo "一键包已生成：$archive"
echo "校验文件：$archive.sha256"
