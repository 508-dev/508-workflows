#!/bin/bash
set -e

echo "Running mypy..."
uv run mypy apps/discord_bot/src/five08/ apps/worker/src/five08/ packages/shared/src/five08/ --ignore-missing-imports
