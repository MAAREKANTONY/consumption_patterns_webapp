#!/usr/bin/env bash
set -euo pipefail
docker compose ps
docker compose logs -n 80 --tail=80 nginx web db
