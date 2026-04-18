#!/bin/bash
set -e

echo "Starting local infrastructure containers..."
docker compose up -d redis postgres minio minio-init

echo
echo "Infrastructure is starting in Docker."
echo "Run app services on the host with:"
echo "  uv run --package api backend-api"
echo "  uv run --package worker worker-consumer"
echo "  uv run --package discord_bot discord-bot"
