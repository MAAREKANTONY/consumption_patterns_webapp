# Consumption Patterns WebApp v1.2 (CLI logic ported)

## What works
- Patterns CRUD (1 row = 1 pattern) storing full JSON definition
- Country selection (FR default) impacts timezone conversion for momenta
- Upload a sales CSV (strict expected columns) and compute:
  - outlet signature: revenue_by_momentum + category_mix_by_momentum
  - euclidean distance vs patterns for selected country
  - probability (softmax on -distance)
- Results page showing signature and ranked patterns

## Expected sales CSV columns (strict MVP)
fyre_id,product_name,cat0,cat1,cat2,price,quantity,datetime

datetime format: `YYYY-MM-DD HH:MM:SS UTC` (UTC), converted to country timezone.

## Run (dev/prod)
docker compose up -d --build
Open: http://localhost:8002 (or via your host Nginx proxy)

## Host Nginx (example)
Proxy https://cpatterns.lifesev.info -> http://127.0.0.1:8002
