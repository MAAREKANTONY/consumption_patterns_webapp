#!/usr/bin/env bash
set -euo pipefail

DOMAIN="cpatterns.lifesev.info"
EMAIL="${EMAIL:-admin@lifesev.info}"

echo "==> Starting stack (HTTP) so nginx can answer ACME challenges..."
docker compose up -d --build nginx web db

echo "==> Requesting Let's Encrypt certificate for ${DOMAIN} ..."
docker compose run --rm certbot certonly \
  --webroot -w /var/www/certbot \
  -d "${DOMAIN}" \
  --email "${EMAIL}" \
  --agree-tos \
  --no-eff-email

echo "==> Reloading nginx with certificates..."
docker compose exec nginx nginx -s reload

echo "✅ HTTPS ready: https://${DOMAIN}"
