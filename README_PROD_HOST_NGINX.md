# Stable v1.1 – Host Nginx Reverse Proxy

This version is designed to run behind an existing Nginx server (not Docker Nginx).

FastAPI container listens on:
127.0.0.1:8002

Nginx host should proxy:
https://cpatterns.lifesev.info -> http://127.0.0.1:8002

Start:
docker compose up -d --build

Update:
git pull
docker compose up -d --build


---

## v1.2 (CLI-aligned) — Patterns by country + Sales import (CSV/XLSX)

This WebApp now embeds the exact same scoring logic as the CLI:

- Outlet signature computation: `core.signature.compute_outlet_signature`
- Scoring vs patterns: `core.distance.score_against_patterns`
- Best pattern selection: `core.decision.select_best_pattern`

### Pages

- `/countries` : manage country profiles (timezone used to compute momenta)
- `/patterns2` : CRUD patterns per country (same JSON "dimensions" structure as CLI patterns files)
- `/run` : upload a sales file (CSV or XLSX) + select country, then compute:
  - DATA QUALITY stats
  - PATTERN PROBABILITIES (normalized)
  - SELECTED pattern

### Sales file expected columns (same as CLI loader)

- Datetime:
  - `datetime` or `purchase_datetime` (ISO or `YYYY-MM-DD HH:MM:SS UTC`)
  - OR `purchase_date` + `purchase_hour`
- Price & quantity:
  - `price` + `quantity`
  - OR `unit_price` + `qty` (fallback)
- Taxonomy (strict, no guessing):
  - `cat0/cat1/cat2`
  - OR `category0/category1/category2`
  - OR `category_produit0/category_produit1/category_produit2`

### Pattern JSON structure

Each Pattern stores **dimensions** only:

- `revenue_by_momentum`: shares over `breakfast,lunch,coffee,apero,dinner,after`
- `category_mix_by_momentum`: per momentum, shares over `food,hot,soft,beer,wine,spirits`

A sample FR patterns file is included at `seeds/patterns_fr.json` and can be imported from the UI.


## Taxonomy roll-up (Preset 2: resto-friendly)

This version rolls up deep taxonomy paths when computing signatures and when importing patterns.
- Food: keep up to category2 (Food > category1 > category2)
- Beverage: keep up to category2, except wine-like branches keep category3 (e.g. Beverage > Adult Beverages > Wines > Red Wines)

A ready-to-import patterns file is included:
`patterns/patterns_FR_market_segments_taxonomy_preset2_rollup.json`


## BigQuery API (optional)

Create a `.env` file from the template:

- copy `.env.example` to `.env`
- set `GCP_PROJECT` and (optionally) `BQ_DATASET`

Credentials are provided via docker `secrets`:
- put your service account JSON at `secrets/gcp_sa_key.json` (not committed)
- container will read it from `/run/secrets/gcp_sa_key` via `GOOGLE_APPLICATION_CREDENTIALS`.

Endpoints:
- `POST /sales/query/by-outlets`
- `POST /sales/query/by-country`
- `POST /score/by-outlets`
- `POST /score/by-country`
