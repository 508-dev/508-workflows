#!/bin/bash
set -e

echo "Running ruff format..."
uv run ruff format apps/discord_bot/src/five08/ apps/worker/src/five08/ packages/shared/src/five08/ tests/
