import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from datetime import date
from io import StringIO
import csv

from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session


# BigQuery is optional. If not installed or not configured, BigQuery endpoints will return a clear error.
try:
    from google.cloud import bigquery  # type: ignore
except Exception:  # pragma: no cover
    bigquery = None  # type: ignore

from .db import engine, SessionLocal, Base
from .models import Country, PatternV2, PremiumProfile, PremiumTicketPercentiles

from core.signature import compute_outlet_signature, rollup_taxonomy_key_from_string
from core.distance import score_against_patterns
from core.decision import select_best_pattern
from core.premiumisation import score_outlet

# NOTE: Some blocks in this file reference `dims` / `pattern_db_id` as locals
# in the pattern save flow. We define safe defaults here to prevent startup
# crashes if those blocks are evaluated elsewhere (they are no-ops outside
# pattern saving).
dims = None
pattern_db_id = None

APP_ROOT = Path(__file__).resolve().parent.parent

app = FastAPI(title="Consumption Patterns WebApp")
templates = Jinja2Templates(directory="app/templates")

Base.metadata.create_all(bind=engine)

# ---------- Helpers ----------
# ---------- BigQuery models ----------

class SalesByOutletsRequest(BaseModel):
    fyre_ids: list[str]
    date_start: date
    date_end: date
    format: str | None = None  # "json" (default) or "csv"

class SalesByCountryRequest(BaseModel):
    country: str
    date_start: date
    date_end: date
    format: str | None = None  # "json" (default) or "csv"


# ---------- Premiumisation models ----------

class PremiumByOutletsRequest(BaseModel):
    fyre_ids: list[str]
    date_start: date
    date_end: date

class PremiumByCountryRequest(BaseModel):
    country: str
    date_start: date
    date_end: date

class PremiumImportRequest(BaseModel):
    profiles: list[dict]

class PremiumRecomputePercentilesRequest(BaseModel):
    country: str
    date_start: date
    date_end: date

def _require_bigquery():
    if bigquery is None:
        raise RuntimeError("BigQuery client library not available. Install google-cloud-bigquery and rebuild the container.")
    project = os.getenv("GCP_PROJECT") or os.getenv("GOOGLE_CLOUD_PROJECT")
    if not project:
        raise RuntimeError("Missing GCP_PROJECT (or GOOGLE_CLOUD_PROJECT) environment variable.")
    return project

def _bq_client():
    project = _require_bigquery()
    return bigquery.Client(project=project)

BQ_DATASET = os.getenv("BQ_DATASET", "datafyre_my_lemonade")

# ---------- Premiumisation BigQuery SQL ----------

def _premium_query_sql_by_outlets():
    return f"""
SELECT
  l.fyre_id,
  oi.order_id,
  oi.price,
  oi.subtotal,
  oi.guests_count,
  oi.quantity,
  oi.created_at,
  cp.brand,
  cp.owner,
  cp.category0,
  cp.category1,
  cp.category2,
  cp.category3,
  cp.category4,
  l.city,
  l.country,
  l.postal_code,
  l.market_segment_type0,
  l.market_segment_type1,
  l.market_segment_type2,
  l.market_segment_type3
FROM `{BQ_DATASET}.Locations` l
LEFT JOIN `{BQ_DATASET}.CatalogProducts` cp
  ON l.fyre_id = cp.fyre_id
LEFT JOIN `{BQ_DATASET}.OrderItems` oi
  ON oi.fyre_id = cp.fyre_id
 AND oi.catalog_id = cp.catalog_id
 AND oi.product_ref = cp.product_ref
 AND oi.sku_ref = cp.sku_ref
WHERE l.fyre_id IN UNNEST(@fyre_ids)
  AND DATE(oi.created_at) >= @date_start
  AND DATE(oi.created_at) <= @date_end
""".strip()


def _premium_query_sql_by_country():
    return f"""
SELECT
  l.fyre_id,
  oi.order_id,
  oi.price,
  oi.subtotal,
  oi.guests_count,
  oi.quantity,
  oi.created_at,
  cp.brand,
  cp.owner,
  cp.category0,
  cp.category1,
  cp.category2,
  cp.category3,
  cp.category4,
  l.city,
  l.country,
  l.postal_code,
  l.market_segment_type0,
  l.market_segment_type1,
  l.market_segment_type2,
  l.market_segment_type3
FROM `{BQ_DATASET}.Locations` l
LEFT JOIN `{BQ_DATASET}.CatalogProducts` cp
  ON l.fyre_id = cp.fyre_id
LEFT JOIN `{BQ_DATASET}.OrderItems` oi
  ON oi.fyre_id = cp.fyre_id
 AND oi.catalog_id = cp.catalog_id
 AND oi.product_ref = cp.product_ref
 AND oi.sku_ref = cp.sku_ref
WHERE l.country = @country
  AND DATE(oi.created_at) >= @date_start
  AND DATE(oi.created_at) <= @date_end
""".strip()


def _bq_fetch_premium_rows_by_outlets(fyre_ids: list[str], date_start: date, date_end: date) -> list[dict]:
    client = _bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("fyre_ids", "STRING", fyre_ids),
            bigquery.ScalarQueryParameter("date_start", "DATE", str(date_start)),
            bigquery.ScalarQueryParameter("date_end", "DATE", str(date_end)),
        ]
    )
    query = _premium_query_sql_by_outlets()
    res = client.query(query, job_config=job_config).result()
    rows=[]
    for row in res:
        rows.append({k: row.get(k) for k in row.keys()})
    return rows


def _bq_fetch_premium_rows_by_country(country: str, date_start: date, date_end: date) -> list[dict]:
    client = _bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("country", "STRING", country.upper()),
            bigquery.ScalarQueryParameter("date_start", "DATE", str(date_start)),
            bigquery.ScalarQueryParameter("date_end", "DATE", str(date_end)),
        ]
    )
    query = _premium_query_sql_by_country()
    res = client.query(query, job_config=job_config).result()
    rows=[]
    for row in res:
        rows.append({k: row.get(k) for k in row.keys()})
    return rows

def _sales_query_sql_by_outlets():
    return f"""
SELECT
  cp.fyre_id,
  cp.product_name,
  cp.category0 AS cat0,
  cp.category1 AS cat1,
  cp.category2 AS cat2,
  cp.category3 AS cat3,
  cp.category4 AS cat4,
  oi.price,
  oi.quantity,
  oi.created_at AS datetime,
  l.country
FROM `{BQ_DATASET}.Locations` l
LEFT JOIN `{BQ_DATASET}.CatalogProducts` cp
  ON l.fyre_id = cp.fyre_id
LEFT JOIN `{BQ_DATASET}.OrderItems` oi
  ON oi.fyre_id = cp.fyre_id
 AND oi.catalog_id = cp.catalog_id
 AND oi.product_ref = cp.product_ref
 AND oi.sku_ref = cp.sku_ref
WHERE cp.fyre_id IN UNNEST(@fyre_ids)
  AND oi.purchase_date >= @date_start
  AND oi.purchase_date <= @date_end
""".strip()

def _sales_query_sql_by_country():
    return f"""
SELECT
  cp.fyre_id,
  cp.product_name,
  cp.category0 AS cat0,
  cp.category1 AS cat1,
  cp.category2 AS cat2,
  cp.category3 AS cat3,
  cp.category4 AS cat4,
  oi.price,
  oi.quantity,
  oi.created_at AS datetime,
  l.country
FROM `{BQ_DATASET}.Locations` l
LEFT JOIN `{BQ_DATASET}.CatalogProducts` cp
  ON l.fyre_id = cp.fyre_id
LEFT JOIN `{BQ_DATASET}.OrderItems` oi
  ON oi.fyre_id = cp.fyre_id
 AND oi.catalog_id = cp.catalog_id
 AND oi.product_ref = cp.product_ref
 AND oi.sku_ref = cp.sku_ref
WHERE l.country = @country
  AND oi.purchase_date >= @date_start
  AND oi.purchase_date <= @date_end
""".strip()

def _rows_to_csv_text(rows: list[dict]) -> str:
    # Keep exactly the expected CSV columns (+ country) for compatibility
    cols = ["fyre_id","product_name","cat0","cat1","cat2","cat3","cat4","price","quantity","datetime","country"]
    buf = StringIO()
    w = csv.DictWriter(buf, fieldnames=cols, delimiter=";")
    w.writeheader()
    for r in rows:
        out = {c: r.get(c, "") for c in cols}
        w.writerow(out)
    return buf.getvalue()

def _bq_fetch_rows_by_outlets(fyre_ids: list[str], date_start: date, date_end: date) -> list[dict]:
    client = _bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ArrayQueryParameter("fyre_ids", "STRING", fyre_ids),
            bigquery.ScalarQueryParameter("date_start", "DATE", str(date_start)),
            bigquery.ScalarQueryParameter("date_end", "DATE", str(date_end)),
        ]
    )
    query = _sales_query_sql_by_outlets()
    res = client.query(query, job_config=job_config).result()
    rows=[]
    for row in res:
        rows.append({k: row.get(k) for k in row.keys()})
    return rows

def _bq_fetch_rows_by_country(country: str, date_start: date, date_end: date) -> list[dict]:
    client = _bq_client()
    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("country", "STRING", country.upper()),
            bigquery.ScalarQueryParameter("date_start", "DATE", str(date_start)),
            bigquery.ScalarQueryParameter("date_end", "DATE", str(date_end)),
        ]
    )
    query = _sales_query_sql_by_country()
    res = client.query(query, job_config=job_config).result()
    rows=[]
    for row in res:
        rows.append({k: row.get(k) for k in row.keys()})
    return rows


def db_session() -> Session:
    return SessionLocal()

def ensure_default_countries():
    defaults = [
        ("FR", "France", "Europe/Paris"),
        ("IL", "Israel", "Asia/Jerusalem"),
        ("UK", "United Kingdom", "Europe/London"),
        ("DE", "Germany", "Europe/Berlin"),
        ("ES", "Spain", "Europe/Madrid"),
        ("IT", "Italy", "Europe/Rome"),
        ("NL", "Netherlands", "Europe/Amsterdam"),
        ("BE", "Belgium", "Europe/Brussels"),
        ("CH", "Switzerland", "Europe/Zurich"),
        ("US", "United States", "America/New_York"),
    ]
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        for code, label, tz in defaults:
            if not db.query(Country).filter(Country.code == code).first():
                db.add(Country(code=code, label=label, timezone=tz))
        db.commit()
    finally:
        db.close()

ensure_default_countries()

def load_patterns_for_country(db: Session, country_code: str):
    patterns = (
        db.query(PatternV2)
        .filter(PatternV2.country_code == country_code)
        .order_by(PatternV2.pattern_id.asc())
        .all()
    )
    out = []
    for p in patterns:
        try:
            dims = json.loads(p.dimensions_json)
        except Exception:
            continue
        out.append({
            "pattern_id": p.pattern_id,
            "label": p.label,
            "country_profile": p.country_code,
            "dimensions": dims,
        })
    return out


def load_premium_profile_for_country(db: Session, country_code: str) -> Optional[dict]:
    prof = (
        db.query(PremiumProfile)
        .filter(PremiumProfile.country_code == country_code)
        .order_by(PremiumProfile.profile_id.asc())
        .first()
    )
    if not prof:
        return None
    try:
        return json.loads(prof.config_json)
    except Exception:
        return None


def load_premium_percentiles(db: Session, country_code: str) -> dict:
    rows = db.query(PremiumTicketPercentiles).filter(PremiumTicketPercentiles.country_code == country_code).all()
    out: dict[str, tuple[float, float, float]] = {}
    for r in rows:
        out[r.segment_key] = (float(r.p50), float(r.p80), float(r.p95))
    return out

# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    return RedirectResponse(url="/run", status_code=302)

# --- Countries ---

@app.get("/countries", response_class=HTMLResponse)
def countries_page(request: Request):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        return templates.TemplateResponse(
            "countries.html",
            {"request": request, "countries": countries},
        )
    finally:
        db.close()

@app.post("/countries")
def upsert_country(
    code: str = Form(...),
    label: str = Form(""),
    timezone: str = Form("UTC"),
):
    code = code.strip().upper()
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        c = db.query(Country).filter(Country.code == code).first()
        if c:
            c.label = label.strip()
            c.timezone = timezone.strip() or "UTC"
        else:
            db.add(Country(code=code, label=label.strip(), timezone=timezone.strip() or "UTC"))
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/countries", status_code=303)

@app.post("/countries/{code}/delete")
def delete_country(code: str):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        c = db.query(Country).filter(Country.code == code).first()
        if c:
            # patterns cascade is not set; delete patterns explicitly
            db.query(PatternV2).filter(PatternV2.country_code == code).delete()
            db.delete(c)
            db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/countries", status_code=303)

# --- Patterns (v2, CLI-aligned) ---

@app.get("/patterns2", response_class=HTMLResponse)
def patterns_page(request: Request, country: Optional[str] = None, msg: Optional[str] = None):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        selected = (country or (countries[0].code if countries else "FR")).upper()

        patterns = (
            db.query(PatternV2)
            .filter(PatternV2.country_code == selected)
            .order_by(PatternV2.pattern_id.asc())
            .all()
        )

        return templates.TemplateResponse(
            "patterns2.html",
            {
                "request": request,
                "countries": countries,
                "selected_country": selected,
                "patterns": patterns,
                "msg": msg,
            },
        )
    finally:
        db.close()

@app.get("/patterns2/new", response_class=HTMLResponse)
def pattern_new(request: Request, country: str = "FR"):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        selected = country.strip().upper()
        empty_dims = {
            "revenue_by_momentum": {k: 0.0 for k in ["breakfast","lunch","coffee","apero","dinner","after"]},
            "category_mix_by_momentum": {m: {b: 0.0 for b in ["food","hot","soft","beer","wine","spirits"]} for m in ["breakfast","lunch","coffee","apero","dinner","after"]},
        }
        return templates.TemplateResponse(
            "pattern_edit.html",
            {
                "request": request,
                "countries": countries,
                "pattern": None,
                "selected_country": selected,
                "dimensions_json": json.dumps(empty_dims, indent=2),
            },
        )
    finally:
        db.close()

@app.get("/patterns2/{pattern_db_id}/edit", response_class=HTMLResponse)
def pattern_edit(request: Request, pattern_db_id: int):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        p = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
        if not p:
            return RedirectResponse(url="/patterns2", status_code=302)
        return templates.TemplateResponse(
            "pattern_edit.html",
            {
                "request": request,
                "countries": countries,
                "pattern": p,
                "selected_country": p.country_code,
                "dimensions_json": p.dimensions_json,
            },
        )
    finally:
        db.close()

@app.post("/patterns2/save")
def pattern_save(
    pattern_db_id: Optional[int] = Form(None),
    country_code: str = Form(...),
    pattern_id: str = Form(...),
    label: str = Form(""),
    dimensions_json: Optional[str] = Form(None),
):
    country_code = country_code.strip().upper()
    pattern_id = pattern_id.strip()

    # If dimensions_json is missing, treat this as a "metadata-only" save.
    # - On create: store an empty scaffold that matches expected shape.
    # - On update: keep existing dimensions_json from DB.

    # Validate JSON early (clear error page) when provided
    if dimensions_json is not None:
        try:
            dims = json.loads(dimensions_json)
            # Basic shape check
            assert "revenue_by_momentum" in dims and "category_mix_by_momentum" in dims
        except Exception:
            url = f"/patterns2/new?country={country_code}"
            if pattern_db_id:
                url = f"/patterns2/{pattern_db_id}/edit"
            return RedirectResponse(url=url, status_code=303)
    else:
        dims = None

    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        if pattern_db_id:
            p = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
            if not p:
                return RedirectResponse(url=f"/patterns2?country={country_code}", status_code=303)
            p.country_code = country_code
            p.pattern_id = pattern_id
            p.label = label.strip()
            p.dimensions_json = dims_to_store
        else:
            p = PatternV2(
                country_code=country_code,
                pattern_id=pattern_id,
                label=label.strip(),
                dimensions_json=dims_to_store,
            )
            db.add(p)
        db.commit()
    finally:
        db.close()

    return RedirectResponse(url=f"/patterns2?country={country_code}", status_code=303)

@app.post("/patterns2/{pattern_db_id}/delete")
def pattern_delete(pattern_db_id: int):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        p = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
        country = p.country_code if p else "FR"
        if p:
            db.delete(p)
            db.commit()
    finally:
        db.close()
    return RedirectResponse(url=f"/patterns2?country={country}", status_code=303)

@app.post("/patterns2/seed/fr")
def seed_fr():
    """Import seeds/patterns_fr.json into DB (idempotent)."""
    seed_path = APP_ROOT / "seeds" / "patterns_fr.json"
    if not seed_path.exists():
        return RedirectResponse(url="/patterns2?country=FR", status_code=303)

    data = json.loads(seed_path.read_text(encoding="utf-8"))
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        for item in data:
            if item.get("country_profile") != "FR":
                continue
            pid = item.get("pattern_id")
            if not pid:
                continue
            existing = (
                db.query(PatternV2)
                .filter(PatternV2.country_code == "FR", PatternV2.pattern_id == pid)
                .first()
            )
            dims = item.get("dimensions", {})
            dims_json = json.dumps(dims, ensure_ascii=False)
            if existing:
                existing.label = item.get("label", existing.label)
                existing.dimensions_json = dims_json
            else:
                db.add(PatternV2(
                    country_code="FR",
                    pattern_id=pid,
                    label=item.get("label",""),
                    dimensions_json=dims_json
                ))
        db.commit()
    finally:
        db.close()
    return RedirectResponse(url="/patterns2?country=FR", status_code=303)

# --- Run / Import sales and score ---



@app.post("/patterns2/import-json")
async def patterns_import_json(
    request: Request,
    file: UploadFile = File(...),
    country_override: Optional[str] = Form(None),
):
    """Bulk import patterns from a JSON file (list of pattern objects).
    Upserts by (country_code, pattern_id)."""
    db = db_session()
    try:
        raw = await file.read()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except UnicodeDecodeError:
            payload = json.loads(raw.decode("utf-8-sig"))
        except Exception:
            return RedirectResponse(url=f"/patterns2?msg=Invalid+JSON+file", status_code=303)

        items = payload if isinstance(payload, list) else [payload]
        if not items:
            return RedirectResponse(url=f"/patterns2?msg=Empty+file", status_code=303)

        created = 0
        updated = 0
        skipped = 0

        for p in items:
            if not isinstance(p, dict):
                skipped += 1
                continue

            cc = (country_override or p.get("country_profile") or p.get("country_code") or "").upper().strip()
            if not cc:
                skipped += 1
                continue

            pid = (p.get("pattern_id") or p.get("id") or "").strip()
            if not pid:
                skipped += 1
                continue

            label = (p.get("label") or pid).strip()
            dims = p.get("dimensions") or {}
            # If the file provides a taxonomy mix inside `category_mix_by_momentum` (keys not in the 6 buckets),
            # promote it to `category_mix_by_momentum_taxonomy` so the distance engine can use it.
            try:
                buckets = {"food","hot","soft","beer","wine","spirits"}
                cm = (dims.get("category_mix_by_momentum") or {})
                if isinstance(cm, dict) and "category_mix_by_momentum_taxonomy" not in dims:
                    found_non_bucket = False
                    for mm, mp in cm.items():
                        if isinstance(mp, dict):
                            for k in mp.keys():
                                if str(k) not in buckets:
                                    found_non_bucket = True
                                    break
                        if found_non_bucket:
                            break
                    if found_non_bucket:
                        dims["category_mix_by_momentum_taxonomy"] = cm
            except Exception:
                pass

            # Preset 2 roll-up: collapse deep taxonomy keys (e.g. Food > meals > pasta > rigattoni -> Food > meals > pasta)
            try:
                cm_tax = dims.get("category_mix_by_momentum_taxonomy")
                if isinstance(cm_tax, dict):
                    rolled = {}
                    for mm, mp in cm_tax.items():
                        if not isinstance(mp, dict):
                            continue
                        agg = {}
                        for k, v in mp.items():
                            rk = rollup_taxonomy_key_from_string(k)
                            if not rk:
                                continue
                            try:
                                val = float(v)
                            except Exception:
                                continue
                            agg[rk] = agg.get(rk, 0.0) + val
                        s = sum(agg.values())
                        if s > 0:
                            agg = {k: vv / s for k, vv in agg.items()}
                        rolled[mm] = agg
                    dims["category_mix_by_momentum_taxonomy"] = rolled
            except Exception:
                pass


            # Create country if missing (safe default)
            cobj = db.query(Country).filter(Country.code == cc).first()
            if cobj is None:
                db.add(Country(code=cc, label=cc, timezone="UTC"))
                db.flush()

            existing = (
                db.query(PatternV2)
                .filter(PatternV2.country_code == cc, PatternV2.pattern_id == pid)
                .first()
            )

            dims_json = json.dumps(dims, ensure_ascii=False)

            if existing:
                existing.label = label
                existing.dimensions_json = dims_json
                updated += 1
            else:
                db.add(PatternV2(country_code=cc, pattern_id=pid, label=label, dimensions_json=dims_json))
                created += 1

        db.commit()
        msg = f"Imported:+{created}+created,+{updated}+updated,+{skipped}+skipped"
        return RedirectResponse(url=f"/patterns2?country={(country_override or (items[0].get('country_profile','') if isinstance(items[0],dict) else '')).upper()}&msg={msg}", status_code=303)
    finally:
        db.close()
# ---------- BigQuery Sales API ----------

@app.post("/sales/query/by-outlets")
def sales_query_by_outlets(payload: SalesByOutletsRequest):
    try:
        rows = _bq_fetch_rows_by_outlets(payload.fyre_ids, payload.date_start, payload.date_end)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    fmt = (payload.format or "json").lower()
    if fmt == "csv":
        return PlainTextResponse(_rows_to_csv_text(rows), media_type="text/csv")
    return {"rows": rows}

@app.post("/sales/query/by-country")
def sales_query_by_country(payload: SalesByCountryRequest):
    try:
        rows = _bq_fetch_rows_by_country(payload.country, payload.date_start, payload.date_end)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    fmt = (payload.format or "json").lower()
    if fmt == "csv":
        return PlainTextResponse(_rows_to_csv_text(rows), media_type="text/csv")
    return {"rows": rows}

@app.post("/score/by-outlets")
def score_by_outlets(payload: SalesByOutletsRequest):
    # Fetch sales rows once, then score per fyre_id using the detected country per outlet
    try:
        rows = _bq_fetch_rows_by_outlets(payload.fyre_ids, payload.date_start, payload.date_end)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    # Group rows by fyre_id
    by_outlet: dict[str, list[dict]] = {}
    for r in rows:
        fid = str(r.get("fyre_id") or "")
        if not fid:
            continue
        by_outlet.setdefault(fid, []).append(r)

    db = db_session()
    try:
        results = {}
        for fid, outlet_rows in by_outlet.items():
            # Detect country (majority / first non-null)
            country = None
            for r in outlet_rows:
                c = r.get("country")
                if c:
                    country = str(c).upper()
                    break
            country = (country or "FR").upper()

            # Build temp CSV and reuse existing signature pipeline
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"cpatterns_bq_{fid}_"))
            tmp_path = tmp_dir / f"bq_{fid}.csv"
            tmp_path.write_text(_rows_to_csv_text(outlet_rows), encoding="utf-8")

            cobj = db.query(Country).filter(Country.code == country).first()
            tz = cobj.timezone if cobj else "UTC"
            patterns = load_patterns_for_country(db, country)

            signature = compute_outlet_signature(str(tmp_path), tz_name=tz)
            stats = signature.get("stats", {})
            out = {"country": country, "signature": signature, "stats": stats, "scores": None, "selected": None, "message": None}

            if stats.get("revenue_total_classified", 0.0) == 0.0:
                out["message"] = "UNCLASSIFIABLE: no classified revenue in this file (all rows unclassified or mapping mismatch)."
            elif not patterns:
                out["message"] = f"UNCLASSIFIABLE: no patterns configured for country {country}."
            else:
                scores = score_against_patterns(signature, patterns)
                if not scores:
                    out["message"] = "UNCLASSIFIABLE: scoring failed (patterns unreadable or total score = 0)."
                else:
                    out["scores"] = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
                    out["selected"] = select_best_pattern(out["scores"])

            results[fid] = out

            try:
                tmp_path.unlink(missing_ok=True); tmp_dir.rmdir()
            except Exception:
                pass

        return {"results": results}
    finally:
        db.close()

@app.post("/score/by-country")
def score_by_country(payload: SalesByCountryRequest):
    try:
        rows = _bq_fetch_rows_by_country(payload.country, payload.date_start, payload.date_end)
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    by_outlet: dict[str, list[dict]] = {}
    for r in rows:
        fid = str(r.get("fyre_id") or "")
        if not fid:
            continue
        by_outlet.setdefault(fid, []).append(r)

    country = payload.country.strip().upper()

    db = db_session()
    try:
        results = {}
        for fid, outlet_rows in by_outlet.items():
            tmp_dir = Path(tempfile.mkdtemp(prefix=f"cpatterns_bq_{fid}_"))
            tmp_path = tmp_dir / f"bq_{fid}.csv"
            tmp_path.write_text(_rows_to_csv_text(outlet_rows), encoding="utf-8")

            cobj = db.query(Country).filter(Country.code == country).first()
            tz = cobj.timezone if cobj else "UTC"
            patterns = load_patterns_for_country(db, country)

            signature = compute_outlet_signature(str(tmp_path), tz_name=tz)
            stats = signature.get("stats", {})
            out = {"country": country, "signature": signature, "stats": stats, "scores": None, "selected": None, "message": None}

            if stats.get("revenue_total_classified", 0.0) == 0.0:
                out["message"] = "UNCLASSIFIABLE: no classified revenue in this file (all rows unclassified or mapping mismatch)."
            elif not patterns:
                out["message"] = f"UNCLASSIFIABLE: no patterns configured for country {country}."
            else:
                scores = score_against_patterns(signature, patterns)
                if not scores:
                    out["message"] = "UNCLASSIFIABLE: scoring failed (patterns unreadable or total score = 0)."
                else:
                    out["scores"] = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
                    out["selected"] = select_best_pattern(out["scores"])

            results[fid] = out

            try:
                tmp_path.unlink(missing_ok=True); tmp_dir.rmdir()
            except Exception:
                pass

        return {"results": results}
    finally:
        db.close()

@app.get("/run", response_class=HTMLResponse)
def run_page(request: Request, country: Optional[str] = None):
    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        selected = (country or (countries[0].code if countries else "FR")).upper()
        patterns_count = db.query(PatternV2).filter(PatternV2.country_code == selected).count()
        return templates.TemplateResponse(
            "run.html",
            {
                "request": request,
                "countries": countries,
                "selected_country": selected,
                "patterns_count": patterns_count,
                "result": None,
            },
        )
    finally:
        db.close()


@app.get("/bq", response_class=HTMLResponse)
def bigquery_page(request: Request):
    """UI to query BigQuery-backed sales endpoints and run scoring (CSV upload UI stays on /run)."""
    db = db_session()
    try:
        countries = db.query(Country).order_by(Country.code.asc()).all()
        return templates.TemplateResponse(
            "bq.html",
            {
                "request": request,
                "countries": countries,
                "gcp_project": os.getenv("GCP_PROJECT"),
                "bq_dataset": os.getenv("BQ_DATASET"),
            },
        )
    finally:
        db.close()

@app.post("/run", response_class=HTMLResponse)
async def run_score(
    request: Request,
    country_code: str = Form(...),
    sales_file: UploadFile = File(...),
):
    country_code = country_code.strip().upper()
    tmp_dir = Path(tempfile.mkdtemp(prefix="cpatterns_"))
    tmp_path = tmp_dir / (sales_file.filename or "sales.csv")

    # Save upload
    content = await sales_file.read()
    tmp_path.write_bytes(content)

    db = db_session()
    try:
        # Decide which dimensions JSON to persist
        if dims is None:
            if pattern_db_id:
                existing = db.query(PatternV2).filter(PatternV2.id == pattern_db_id).first()
                dims_to_store = existing.dimensions_json if existing else json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
            else:
                dims_to_store = json.dumps({
                    "revenue_by_momentum": {},
                    "category_mix_by_momentum": {}
                })
        else:
            dims_to_store = json.dumps(dims, ensure_ascii=False)
        countries = db.query(Country).order_by(Country.code.asc()).all()
        c = db.query(Country).filter(Country.code == country_code).first()
        tz = c.timezone if c else "UTC"

        patterns = load_patterns_for_country(db, country_code)

        signature = compute_outlet_signature(str(tmp_path), tz_name=tz)
        stats = signature.get("stats", {})

        result = {
            "signature": signature,
            "stats": stats,
            "scores": None,
            "selected": None,
            "message": None,
        }

        if stats.get("revenue_total_classified", 0.0) == 0.0:
            result["message"] = "UNCLASSIFIABLE: no classified revenue in this file (all rows unclassified or mapping mismatch)."
        elif not patterns:
            result["message"] = f"UNCLASSIFIABLE: no patterns configured for country {country_code}."
        else:
            scores = score_against_patterns(signature, patterns)
            if not scores:
                result["message"] = "UNCLASSIFIABLE: scoring failed (patterns unreadable or total score = 0)."
            else:
                best = select_best_pattern(scores)
                result["scores"] = dict(sorted(scores.items(), key=lambda x: x[1], reverse=True))
                result["selected"] = best

        patterns_count = len(patterns)
        return templates.TemplateResponse(
            "run.html",
            {
                "request": request,
                "countries": countries,
                "selected_country": country_code,
                "patterns_count": patterns_count,
                "result": result,
            },
        )
    finally:
        db.close()
        try:
            tmp_path.unlink(missing_ok=True)
            tmp_dir.rmdir()
        except Exception:
            pass


# ---------- Premiumisation UI & API ----------


@app.get("/premiumisation", response_class=HTMLResponse)
def premiumisation_page(request: Request, country: str = "FR"):
    db = db_session()
    try:
        countries = db.query(Country).order_by(Country.code.asc()).all()
        prof = db.query(PremiumProfile).filter(PremiumProfile.country_code == country.upper()).first()
        prof_json = prof.config_json if prof else ""
        pct_rows = db.query(PremiumTicketPercentiles).filter(PremiumTicketPercentiles.country_code == country.upper()).all()
        return templates.TemplateResponse(
            "premiumisation.html",
            {
                "request": request,
                "countries": countries,
                "selected_country": country.upper(),
                "profile_json": prof_json,
                "percentiles": pct_rows,
            },
        )
    finally:
        db.close()


@app.get("/premiumisation/api/profile")
def get_premium_profile(country: str = "FR"):
    db = db_session()
    try:
        prof = db.query(PremiumProfile).filter(PremiumProfile.country_code == country.upper()).first()
        return {"country": country.upper(), "profile": json.loads(prof.config_json) if prof else None}
    finally:
        db.close()


@app.post("/premiumisation/api/profile/save")
def save_premium_profile(country: str = Form(...), profile_json: str = Form(...)):
    db = db_session()
    try:
        cc = country.upper()
        parsed = json.loads(profile_json)
        profile_id = parsed.get("profile_id") or f"{cc}_v1"
        label = parsed.get("label") or ""
        existing = db.query(PremiumProfile).filter(PremiumProfile.country_code == cc, PremiumProfile.profile_id == profile_id).first()
        if existing:
            existing.label = label
            existing.config_json = json.dumps(parsed, ensure_ascii=False)
        else:
            db.add(PremiumProfile(country_code=cc, profile_id=profile_id, label=label, config_json=json.dumps(parsed, ensure_ascii=False)))
        db.commit()
        return {"ok": True, "country": cc, "profile_id": profile_id}
    finally:
        db.close()


@app.post("/premiumisation/api/profile/import")
async def import_premium_profile(file: UploadFile = File(...)):
    raw = (await file.read()).decode("utf-8", errors="ignore")
    data = json.loads(raw)
    profiles = data if isinstance(data, list) else [data]
    db = db_session()
    try:
        count = 0
        for p in profiles:
            cc = str(p.get("country_iso2") or p.get("country") or "").upper()
            if not cc:
                continue
            pid = str(p.get("profile_id") or f"{cc}_v1")
            label = str(p.get("label") or "")
            existing = db.query(PremiumProfile).filter(PremiumProfile.country_code == cc, PremiumProfile.profile_id == pid).first()
            if existing:
                existing.label = label
                existing.config_json = json.dumps(p, ensure_ascii=False)
            else:
                db.add(PremiumProfile(country_code=cc, profile_id=pid, label=label, config_json=json.dumps(p, ensure_ascii=False)))
            count += 1
        db.commit()
        return {"ok": True, "imported": count}
    finally:
        db.close()


@app.post("/premiumisation/api/percentiles/recompute")
def recompute_percentiles(req: PremiumRecomputePercentilesRequest):
    _require_bigquery()
    client = _bq_client()
    country = req.country.upper()

    sql = f"""
WITH tickets AS (
  SELECT
    l.country AS country,
    LOWER(CONCAT(
      COALESCE(l.market_segment_type0,''),' > ',
      COALESCE(l.market_segment_type1,''),' > ',
      COALESCE(l.market_segment_type2,''),' > ',
      COALESCE(l.market_segment_type3,'')
    )) AS segment_key,
    oi.order_id AS order_id,
    SUM(oi.subtotal) AS ticket_value
  FROM `{BQ_DATASET}.Locations` l
  LEFT JOIN `{BQ_DATASET}.CatalogProducts` cp
    ON l.fyre_id = cp.fyre_id
  LEFT JOIN `{BQ_DATASET}.OrderItems` oi
    ON oi.fyre_id = cp.fyre_id
   AND oi.catalog_id = cp.catalog_id
   AND oi.product_ref = cp.product_ref
   AND oi.sku_ref = cp.sku_ref
  WHERE l.country = @country
    AND DATE(oi.created_at) >= @date_start
    AND DATE(oi.created_at) <= @date_end
    AND oi.order_id IS NOT NULL
  GROUP BY country, segment_key, order_id
),
seg AS (
  SELECT
    country,
    segment_key,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(50)] AS p50,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(80)] AS p80,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(95)] AS p95
  FROM tickets
  GROUP BY country, segment_key
),
allc AS (
  SELECT
    country,
    'country_all' AS segment_key,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(50)] AS p50,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(80)] AS p80,
    APPROX_QUANTILES(ticket_value, 100)[OFFSET(95)] AS p95
  FROM tickets
  GROUP BY country
)
SELECT * FROM seg
UNION ALL
SELECT * FROM allc
""".strip()

    job_config = bigquery.QueryJobConfig(
        query_parameters=[
            bigquery.ScalarQueryParameter("country", "STRING", country),
            bigquery.ScalarQueryParameter("date_start", "DATE", str(req.date_start)),
            bigquery.ScalarQueryParameter("date_end", "DATE", str(req.date_end)),
        ]
    )
    res = client.query(sql, job_config=job_config).result()
    rows = [{k: row.get(k) for k in row.keys()} for row in res]

    db = db_session()
    try:
        for r in rows:
            seg_key = str(r.get("segment_key") or "").strip().lower()
            if not seg_key:
                continue
            p50 = float(r.get("p50") or 0)
            p80 = float(r.get("p80") or 0)
            p95 = float(r.get("p95") or 0)
            existing = db.query(PremiumTicketPercentiles).filter(
                PremiumTicketPercentiles.country_code == country,
                PremiumTicketPercentiles.segment_key == seg_key,
            ).first()
            if existing:
                existing.p50 = p50
                existing.p80 = p80
                existing.p95 = p95
            else:
                db.add(PremiumTicketPercentiles(country_code=country, segment_key=seg_key, p50=p50, p80=p80, p95=p95))
        db.commit()
        return {"ok": True, "country": country, "rows": len(rows)}
    finally:
        db.close()


@app.post("/premiumisation/api/score/by-outlets")
def premium_score_by_outlets(req: PremiumByOutletsRequest):
    _require_bigquery()
    rows = _bq_fetch_premium_rows_by_outlets(req.fyre_ids, req.date_start, req.date_end)
    if not rows:
        return {"ok": True, "results": {}}
    by_outlet: dict[str, list[dict]] = {}
    for r in rows:
        fid = str(r.get("fyre_id") or "")
        if fid:
            by_outlet.setdefault(fid, []).append(r)

    db = db_session()
    try:
        results = {}
        for fid, rws in by_outlet.items():
            country = str((rws[0] or {}).get("country") or "").upper()
            prof = load_premium_profile_for_country(db, country)
            if not prof:
                results[fid] = {"error": f"No premium profile for country {country}"}
                continue
            pct = load_premium_percentiles(db, country)
            res = score_outlet(rws, prof, pct)
            results[fid] = res.__dict__
        return {"ok": True, "results": results}
    finally:
        db.close()


@app.post("/premiumisation/api/score/by-country")
def premium_score_by_country(req: PremiumByCountryRequest):
    _require_bigquery()
    rows = _bq_fetch_premium_rows_by_country(req.country, req.date_start, req.date_end)
    if not rows:
        return {"ok": True, "results": {}}
    by_outlet: dict[str, list[dict]] = {}
    for r in rows:
        fid = str(r.get("fyre_id") or "")
        if fid:
            by_outlet.setdefault(fid, []).append(r)

    db = db_session()
    try:
        country = req.country.upper()
        prof = load_premium_profile_for_country(db, country)
        if not prof:
            return {"ok": False, "error": f"No premium profile for country {country}"}
        pct = load_premium_percentiles(db, country)
        results = {}
        for fid, rws in by_outlet.items():
            res = score_outlet(rws, prof, pct)
            results[fid] = res.__dict__
        return {"ok": True, "results": results}
    finally:
        db.close()