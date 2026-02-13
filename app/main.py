import json
import os
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, Form, UploadFile, File
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import engine, SessionLocal, Base
from .models import Country, PatternV2

from core.signature import compute_outlet_signature
from core.distance import score_against_patterns
from core.decision import select_best_pattern

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