#!/usr/bin/env bash
set -euo pipefail

echo "==> Pulling latest code..."
git pull

echo "==> Rebuilding and restarting..."
docker compose up -d --build

echo "✅ Updated."
