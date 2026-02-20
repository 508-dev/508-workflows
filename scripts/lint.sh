#!/bin/bash
set -e

echo "Running ruff check..."
uv run ruff check apps/discord_bot/src/five08/ apps/worker/src/five08/ packages/shared/src/five08/ tests/
