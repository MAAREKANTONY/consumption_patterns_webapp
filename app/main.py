import json
import os
import uuid
from typing import Dict, List, Tuple

import pandas as pd
from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .db import engine, SessionLocal, Base
from .models import Pattern

app = FastAPI(title="Consumption Patterns WebApp")

templates = Jinja2Templates(directory="app/templates")

Base.metadata.create_all(bind=engine)

UPLOAD_DIR = os.getenv("UPLOAD_DIR", "/tmp/cpatterns_uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

# In-memory mapping upload_id -> absolute file path
UPLOADS: Dict[str, str] = {}


def _read_sales_file(path: str) -> pd.DataFrame:
    """Read CSV or XLSX into a dataframe."""
    lower = path.lower()
    if lower.endswith(".csv"):
        # Try utf-8 then fallback
        try:
            return pd.read_csv(path)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="latin-1")
    if lower.endswith(".xlsx") or lower.endswith(".xls"):
        return pd.read_excel(path)
    raise ValueError("Unsupported file type. Please upload a .csv, .xlsx or .xls")


def _signature_from_sales(df: pd.DataFrame, category_col: str, value_col: str) -> Dict[str, float]:
    d = df.copy()
    d[category_col] = d[category_col].astype(str).str.strip().str.lower()
    # Coerce value to numeric
    d[value_col] = pd.to_numeric(d[value_col], errors="coerce")
    d = d.dropna(subset=[category_col, value_col])
    grouped = d.groupby(category_col)[value_col].sum()
    total = float(grouped.sum())
    if total <= 0:
        return {}
    sig = (grouped / total).to_dict()
    # Remove empty keys
    return {k: float(v) for k, v in sig.items() if k and float(v) > 0}


def _euclidean_distance(sig: Dict[str, float], pattern: Dict[str, float]) -> float:
    keys = set(sig.keys()) | set(pattern.keys())
    s = 0.0
    for k in keys:
        a = float(sig.get(k, 0.0))
        b = float(pattern.get(k, 0.0))
        s += (a - b) ** 2
    return float(s ** 0.5)


def _parse_pattern_json(raw: str) -> Dict[str, float]:
    """Accept either {"features": {...}} or directly {...}."""
    obj = json.loads(raw) if raw else {}
    if isinstance(obj, dict) and "features" in obj and isinstance(obj["features"], dict):
        obj = obj["features"]
    if not isinstance(obj, dict):
        return {}
    out: Dict[str, float] = {}
    for k, v in obj.items():
        try:
            kk = str(k).strip().lower()
            vv = float(v)
            if kk and vv >= 0:
                out[kk] = vv
        except Exception:
            continue
    # normalize (optional)
    total = sum(out.values())
    if total > 0:
        out = {k: v / total for k, v in out.items()}
    return out

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


# -------------------------
# Patterns CRUD (by country)
# -------------------------

@app.get("/patterns", response_class=HTMLResponse)
def patterns_list(request: Request, country: str = ""):
    db: Session = SessionLocal()
    q = db.query(Pattern)
    if country:
        q = q.filter(Pattern.country.ilike(country))
    patterns = q.order_by(Pattern.country.asc(), Pattern.name.asc()).all()
    countries = [c[0] for c in db.query(Pattern.country).distinct().order_by(Pattern.country.asc()).all()]
    db.close()
    return templates.TemplateResponse(
        "patterns_list.html",
        {"request": request, "patterns": patterns, "countries": countries, "country": country},
    )


@app.get("/patterns/new", response_class=HTMLResponse)
def pattern_new(request: Request):
    return templates.TemplateResponse(
        "pattern_form.html",
        {"request": request, "mode": "new", "pattern": None},
    )


@app.post("/patterns/new")
def pattern_create(
    name: str = Form(...),
    country: str = Form(...),
    features_json: str = Form(""),
):
    # Validate JSON early to give clean errors
    try:
        _ = _parse_pattern_json(features_json)
    except Exception as e:
        return HTMLResponse(f"Invalid JSON: {e}", status_code=400)

    db: Session = SessionLocal()
    p = Pattern(name=name.strip(), country=country.strip().upper(), json_definition=features_json)
    db.add(p)
    db.commit()
    db.close()
    return RedirectResponse(url="/patterns", status_code=303)


@app.get("/patterns/{pattern_id}/edit", response_class=HTMLResponse)
def pattern_edit(request: Request, pattern_id: int):
    db: Session = SessionLocal()
    p = db.query(Pattern).filter(Pattern.id == pattern_id).first()
    db.close()
    if not p:
        return HTMLResponse("Pattern not found", status_code=404)
    return templates.TemplateResponse(
        "pattern_form.html",
        {"request": request, "mode": "edit", "pattern": p},
    )


@app.post("/patterns/{pattern_id}/edit")
def pattern_update(
    pattern_id: int,
    name: str = Form(...),
    country: str = Form(...),
    features_json: str = Form(""),
):
    try:
        _ = _parse_pattern_json(features_json)
    except Exception as e:
        return HTMLResponse(f"Invalid JSON: {e}", status_code=400)

    db: Session = SessionLocal()
    p = db.query(Pattern).filter(Pattern.id == pattern_id).first()
    if not p:
        db.close()
        return HTMLResponse("Pattern not found", status_code=404)
    p.name = name.strip()
    p.country = country.strip().upper()
    p.json_definition = features_json
    db.commit()
    db.close()
    return RedirectResponse(url="/patterns", status_code=303)


@app.post("/patterns/{pattern_id}/delete")
def pattern_delete(pattern_id: int):
    db: Session = SessionLocal()
    p = db.query(Pattern).filter(Pattern.id == pattern_id).first()
    if p:
        db.delete(p)
        db.commit()
    db.close()
    return RedirectResponse(url="/patterns", status_code=303)


# -------------------------
# Imports & scoring
# -------------------------

@app.get("/imports", response_class=HTMLResponse)
def imports_home(request: Request):
    db: Session = SessionLocal()
    countries = [c[0] for c in db.query(Pattern.country).distinct().order_by(Pattern.country.asc()).all()]
    db.close()
    return templates.TemplateResponse(
        "imports.html",
        {"request": request, "countries": countries},
    )


@app.post("/imports/preview", response_class=HTMLResponse)
async def imports_preview(
    request: Request,
    country: str = Form(...),
    file: UploadFile = File(...),
):
    # Save upload
    ext = os.path.splitext(file.filename or "")[-1].lower() or ".csv"
    upload_id = str(uuid.uuid4())
    path = os.path.join(UPLOAD_DIR, f"{upload_id}{ext}")
    with open(path, "wb") as f:
        f.write(await file.read())
    UPLOADS[upload_id] = path

    # Read a preview
    try:
        df = _read_sales_file(path)
    except Exception as e:
        return HTMLResponse(f"Could not read file: {e}", status_code=400)

    columns = list(df.columns)
    preview_rows = df.head(15).fillna("").astype(str).to_dict(orient="records")

    # Heuristics for defaults
    category_guess = next((c for c in columns if str(c).lower() in ["category", "categorie", "item", "produit", "product", "sku", "brand"]), columns[0] if columns else "")
    value_guess = next((c for c in columns if str(c).lower() in ["amount", "revenue", "ca", "sales", "qty", "quantity", "volume", "value"]), "")

    return templates.TemplateResponse(
        "import_preview.html",
        {
            "request": request,
            "country": country.strip().upper(),
            "upload_id": upload_id,
            "filename": file.filename,
            "columns": columns,
            "preview_rows": preview_rows,
            "category_guess": category_guess,
            "value_guess": value_guess,
        },
    )


@app.post("/imports/compute", response_class=HTMLResponse)
def imports_compute(
    request: Request,
    country: str = Form(...),
    upload_id: str = Form(...),
    category_col: str = Form(...),
    value_col: str = Form(...),
):
    path = UPLOADS.get(upload_id)
    if not path or not os.path.exists(path):
        return HTMLResponse("Upload expired or not found. Please re-upload.", status_code=400)

    try:
        df = _read_sales_file(path)
    except Exception as e:
        return HTMLResponse(f"Could not read file: {e}", status_code=400)

    if category_col not in df.columns or value_col not in df.columns:
        return HTMLResponse("Selected columns not found in file.", status_code=400)

    signature = _signature_from_sales(df, category_col, value_col)

    db: Session = SessionLocal()
    patterns = (
        db.query(Pattern)
        .filter(Pattern.country.ilike(country.strip().upper()))
        .order_by(Pattern.name.asc())
        .all()
    )
    scored: List[Tuple[Pattern, float]] = []
    for p in patterns:
        try:
            p_vec = _parse_pattern_json(p.json_definition)
        except Exception:
            p_vec = {}
        dist = _euclidean_distance(signature, p_vec)
        scored.append((p, dist))

    scored.sort(key=lambda x: x[1])
    db.close()

    # Top categories for display
    top_sig = sorted(signature.items(), key=lambda kv: kv[1], reverse=True)[:20]

    return templates.TemplateResponse(
        "import_results.html",
        {
            "request": request,
            "country": country.strip().upper(),
            "category_col": category_col,
            "value_col": value_col,
            "signature": signature,
            "top_sig": top_sig,
            "scored": scored,
        },
    )
