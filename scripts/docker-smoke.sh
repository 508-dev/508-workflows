#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
repo_root=$(CDPATH= cd "$script_dir/.." && pwd)
project="five08-smoke-$(date +%s)-$$"
compose_file=$(mktemp "${TMPDIR:-/tmp}/five08-docker-smoke.XXXXXX")
body_file=$(mktemp "${TMPDIR:-/tmp}/five08-docker-smoke-body.XXXXXX")

cleanup() {
  docker compose -p "$project" -f "$compose_file" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -f "$compose_file" "$body_file"
}
trap cleanup EXIT HUP INT TERM

cat >"$compose_file" <<EOF
services:
  redis:
    image: redis:7-alpine
    command: ["redis-server", "--save", "60", "1", "--loglevel", "warning"]
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 2s
      timeout: 2s
      retries: 15

  postgres:
    image: postgres:17-alpine
    environment:
      POSTGRES_DB: workflows
      POSTGRES_USER: postgres
      POSTGRES_PASSWORD: postgres
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres -d workflows"]
      interval: 2s
      timeout: 2s
      retries: 15

  web:
    build:
      context: "$repo_root"
      dockerfile: apps/api/Dockerfile
    command: ["uv", "run", "--package", "api", "backend-api"]
    environment:
      ENVIRONMENT: production
      API_SHARED_SECRET: smoke-secret
      WEB_HOST: 0.0.0.0
      WEB_PORT: "8090"
      REDIS_URL: redis://redis:6379/0
      REDIS_QUEUE_NAME: jobs.default
      POSTGRES_URL: postgresql://postgres:postgres@postgres:5432/workflows
      MINIO_ENDPOINT: http://minio.invalid:9000
      MINIO_ROOT_USER: internal
      MINIO_ROOT_PASSWORD: smoke-secret
      MINIO_INTERNAL_BUCKET: internal-transfers
      CRM_SYNC_ENABLED: "true"
      ESPO_BASE_URL: ""
      ESPO_API_KEY: ""
      NEWSLETTER_SYNC_ENABLED: "false"
      EMAIL_RESUME_INTAKE_ENABLED: "false"
      INTAKE_RESUME_REQUIRE_VIRUS_SCAN: "false"
    ports:
      - "127.0.0.1::8090"
    depends_on:
      redis:
        condition: service_healthy
      postgres:
        condition: service_healthy
EOF

echo "Starting Docker smoke stack: $project"
docker compose -p "$project" -f "$compose_file" up -d --build redis postgres web

web_port=""
attempts=0
while [ "$attempts" -lt 60 ]; do
  web_port=$(docker compose -p "$project" -f "$compose_file" port web 8090 | sed 's/.*://')
  if [ -n "$web_port" ]; then
    break
  fi
  attempts=$((attempts + 1))
  sleep 1
done

if [ -z "$web_port" ]; then
  echo "web service did not publish a host port" >&2
  docker compose -p "$project" -f "$compose_file" logs web >&2 || true
  exit 1
fi

health_url="http://127.0.0.1:$web_port/health"
echo "Waiting for $health_url"
last_code=""
attempts=0
while [ "$attempts" -lt 60 ]; do
  if [ "$(docker inspect -f '{{.State.Running}}' "$(docker compose -p "$project" -f "$compose_file" ps -q web)" 2>/dev/null || echo false)" != "true" ]; then
    echo "web service exited before health check succeeded" >&2
    docker compose -p "$project" -f "$compose_file" logs web >&2 || true
    exit 1
  fi

  last_code=$(curl -sS -o "$body_file" -w "%{http_code}" "$health_url" || true)
  if [ "$last_code" = "200" ] && grep -q '"status":"healthy"' "$body_file"; then
    echo "Docker smoke check passed."
    exit 0
  fi
  attempts=$((attempts + 1))
  sleep 1
done

echo "Docker smoke check failed; last HTTP status: ${last_code:-none}" >&2
cat "$body_file" >&2 || true
echo >&2
docker compose -p "$project" -f "$compose_file" logs web >&2 || true
exit 1
